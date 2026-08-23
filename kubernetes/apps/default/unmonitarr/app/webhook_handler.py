import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from .database import get_db_session
from .models import (
    MediaItem, SonarrMapping, RadarrMapping, SyncLog, 
    MediaType, SyncAction, SyncStatus
)
from .jellyfin_client import JellyfinClient
from .sonarr_client import SonarrClient
from .radarr_client import RadarrClient
from .external_api_client import ExternalAPIClient
from .config import settings

logger = logging.getLogger(__name__)


class WebhookHandler:
    """Handle webhooks from Jellyfin and coordinate with Sonarr/Radarr."""
    
    def __init__(
        self, 
        jellyfin_client: JellyfinClient,
        sonarr_client: SonarrClient,
        radarr_client: RadarrClient,
        external_api_client: ExternalAPIClient
    ):
        self.jellyfin_client = jellyfin_client
        self.sonarr_client = sonarr_client
        self.radarr_client = radarr_client
        self.external_api_client = external_api_client
        self._processing_cache = {}  # To prevent duplicate processing
    
    async def process_webhook(self, payload: Dict[str, Any], force_sync: bool = False) -> None:
        """Process incoming webhook from Jellyfin.
        
        Args:
            payload: Webhook data from Jellyfin or pre-processed webhook data
            force_sync: If True, bypass status change detection and force sync
        """
        try:
            # Check if this is a retry operation
            is_retry = payload.get("retry_mode", False)
            original_log_id = payload.get("original_log_id")
            
            if is_retry:
                logger.info(f"🔄 Processing retry operation for log ID {original_log_id}")
            else:
                logger.info(f"Processing webhook payload: {payload}")
            
            # Check if this is already processed webhook data (from bulk sync or retry)
            if self._is_processed_webhook_data(payload):
                if is_retry:
                    logger.info("Detected retry webhook data")
                else:
                    logger.info("Detected pre-processed webhook data from bulk sync")
                webhook_data = payload
            else:
                # Extract webhook data from raw Jellyfin webhook
                webhook_data = self._extract_webhook_data(payload)
                if not webhook_data:
                    logger.warning("Could not extract valid data from webhook payload")
                    return
            
            # Check if this is a UserDataSaved event with watched status change
            if not self._is_watched_status_change(webhook_data):
                logger.debug("Webhook is not a watched status change, ignoring")
                return
            
            # Prevent duplicate processing
            is_watched = webhook_data.get('is_watched', 'unknown')
            cache_key = f"{webhook_data['jellyfin_id']}_{webhook_data['user_id']}_{is_watched}"
            if cache_key in self._processing_cache:
                logger.debug(f"Already processing {cache_key}, skipping")
                return
            
            self._processing_cache[cache_key] = datetime.utcnow()
            
            try:
                # Add delay to prevent rapid-fire updates
                await asyncio.sleep(settings.sync_delay_seconds)
                
                # Process the watched status change
                await self._process_watched_status_change(
                    webhook_data, 
                    force_sync=force_sync, 
                    is_retry=is_retry, 
                    original_log_id=original_log_id
                )
                
            finally:
                # Clean up cache after processing
                self._processing_cache.pop(cache_key, None)
                
        except Exception as e:
            logger.error(f"Error processing webhook: {e}")
    
    
    def _is_processed_webhook_data(self, payload: Dict[str, Any]) -> bool:
        """Check if payload is already processed webhook data (from bulk sync).
        
        Processed webhook data has these characteristics:
        - Contains 'jellyfin_id' instead of 'ItemId'
        - Contains 'user_id' instead of 'UserId'  
        - Contains 'event_type' field
        - Contains 'is_watched' field
        """
        required_keys = ['jellyfin_id', 'user_id', 'event_type', 'is_watched']
        return all(key in payload for key in required_keys)
    
    def _extract_webhook_data(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract relevant data from webhook payload."""
        try:
            logger.debug(f"Full webhook payload structure: {payload}")
            
            # Handle "Send All" format from Jellyfin (when template is ignored)
            # This format includes full event data structure
            if "NotificationType" in payload or "Event" in payload:
                logger.info("Detected Jellyfin 'Send All' format webhook")
                return self._extract_send_all_format(payload)
            
            # Handle template format (legacy)
            template = payload.get("template", {})
            
            # Get event type from various possible locations
            event_type = (
                template.get("Type") or 
                payload.get("Type") or 
                payload.get("event_type") or
                payload.get("NotificationType")
            )
            
            # Check if this is a UserDataSaved event
            if event_type != "UserDataSaved":
                events = payload.get("events", [])
                if "UserDataSaved" not in events:
                    logger.debug(f"Not a UserDataSaved event: {event_type}, events: {events}")
                    return None
                event_type = "UserDataSaved"
            
            # Extract item data from multiple possible locations
            item_id = (
                template.get("ItemId") or 
                payload.get("ItemId") or 
                payload.get("Id") or
                payload.get("Item", {}).get("Id")
            )
            user_id = (
                template.get("UserId") or 
                payload.get("UserId") or 
                payload.get("user_id") or
                payload.get("User", {}).get("Id")
            )
            
            logger.debug(f"Extracted: event_type={event_type}, item_id={item_id}, user_id={user_id}")
            
            if not item_id or not user_id:
                logger.warning(f"Missing ItemId or UserId in webhook payload. ItemId: {item_id}, UserId: {user_id}")
                return None
            
            return {
                "event_type": event_type,
                "jellyfin_id": item_id,
                "user_id": user_id,
                "timestamp": datetime.utcnow()
            }
            
        except Exception as e:
            logger.error(f"Error extracting webhook data: {e}")
            return None
    
    def _extract_send_all_format(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract data from Jellyfin 'Send All' format webhook."""
        try:
            # In "Send All" format, event type is in NotificationType
            event_type = payload.get("NotificationType")
            
            if event_type != "UserDataSaved":
                logger.debug(f"Not a UserDataSaved event: {event_type}")
                return None
            
            # In this format, data is directly in the payload, not nested in Item/User objects
            item_id = payload.get("ItemId")
            user_id = payload.get("UserId")
            item_name = payload.get("Name")
            item_type = payload.get("ItemType")
            username = payload.get("NotificationUsername")
            is_played = payload.get("Played", False)
            save_reason = payload.get("SaveReason")
            
            logger.info(f"Send All format - Item: '{item_name}' ({item_type}), User: {username}")
            logger.info(f"Played status: {is_played}, Save reason: {save_reason}")
            logger.debug(f"Item ID: {item_id}, User ID: {user_id}")
            
            if not item_id or not user_id:
                logger.warning(f"Missing ItemId or UserId in Send All format. ItemId: {item_id}, UserId: {user_id}")
                return None
            
            # Only process if this is actually a watched status change
            # Accept multiple SaveReasons:
            # - TogglePlayed: Manual toggle of played status
            # - PlaybackFinished: When playback completes naturally (episode finished)
            # Note: PlaybackProgress is too frequent and not relevant for watched status
            if save_reason not in ["TogglePlayed", "PlaybackFinished"]:
                logger.debug(f"Not a relevant event - SaveReason: {save_reason}")
                return None
            
            return {
                "event_type": event_type,
                "jellyfin_id": item_id,
                "user_id": user_id,
                "is_watched": is_played,  # Include the watched status directly
                "item_name": item_name,
                "item_type": item_type,
                "username": username,
                "timestamp": datetime.utcnow(),
                # Extract series/season info from webhook for episodes
                "series_id": payload.get("SeriesId"),
                "series_name": payload.get("SeriesName"),
                "season_id": payload.get("SeasonId"),
                "season_number": payload.get("SeasonNumber"),
                "episode_number": payload.get("EpisodeNumber"),
                # Extract external provider IDs for better matching (try multiple field variations)
                "tvdb_id": (
                    payload.get("Provider_tvdb") or 
                    payload.get("ProviderIds", {}).get("Tvdb") or
                    payload.get("ProviderIds", {}).get("tvdb") or
                    payload.get("TvdbId") or
                    payload.get("tvdbId")
                ),
                "imdb_id": (
                    payload.get("Provider_imdb") or 
                    payload.get("ProviderIds", {}).get("Imdb") or
                    payload.get("ProviderIds", {}).get("imdb") or
                    payload.get("ImdbId") or
                    payload.get("imdbId")
                ),
                "tmdb_id": (
                    payload.get("Provider_tmdb") or 
                    payload.get("ProviderIds", {}).get("Tmdb") or
                    payload.get("ProviderIds", {}).get("tmdb") or
                    payload.get("TmdbId") or
                    payload.get("tmdbId")
                ),
                "year": payload.get("Year")
            }
            
        except Exception as e:
            logger.error(f"Error extracting Send All format data: {e}")
            return None
    
    def _normalize_media_type(self, jellyfin_type: str) -> str:
        """Normalize Jellyfin media type to our MediaType enum."""
        type_mapping = {
            "Episode": MediaType.EPISODE,
            "Movie": MediaType.MOVIE,
            "Series": MediaType.SERIES,
            "Season": MediaType.SEASON,
            # Lowercase variants
            "episode": MediaType.EPISODE,
            "movie": MediaType.MOVIE,
            "series": MediaType.SERIES,
            "season": MediaType.SEASON
        }
        
        normalized = type_mapping.get(jellyfin_type, MediaType.EPISODE)  # Default to episode
        logger.debug(f"Normalized media type: {jellyfin_type} -> {normalized}")
        return normalized
    
    def _is_watched_status_change(self, webhook_data: Dict[str, Any]) -> bool:
        """Check if this webhook represents a watched status change."""
        # For UserDataSaved events, we need to be more intelligent
        # This is a simplified check - in reality, we'd compare with previous state
        return webhook_data.get("event_type") == "UserDataSaved"
    
    async def _process_watched_status_change(
        self, 
        webhook_data: Dict[str, Any], 
        force_sync: bool = False, 
        is_retry: bool = False, 
        original_log_id: Optional[int] = None
    ) -> None:
        """Process a watched status change from Jellyfin.
        
        Args:
            webhook_data: Extracted webhook data from Jellyfin
            force_sync: If True, bypass status change detection and force sync
        """
        try:
            jellyfin_id = webhook_data["jellyfin_id"]
            user_id = webhook_data["user_id"]
            
            # Check if this is a special episode and should be ignored
            season_number = webhook_data.get("season_number")
            if season_number == 0 and settings.ignore_special_episodes:
                logger.info(f"Ignoring special episode (season 0) for item {jellyfin_id} due to configuration")
                return
            
            # Check if watched status is directly available in webhook data
            if "is_watched" in webhook_data:
                is_watched = webhook_data["is_watched"]
                item_name = webhook_data.get("item_name", "Unknown")
                item_type = webhook_data.get("item_type", "Unknown")
                logger.info(f"Using watched status from webhook: {item_name} -> watched={is_watched}")
            else:
                # Fallback: Fetch current item state from Jellyfin
                logger.info("Fetching current watched status from Jellyfin API")
                item_data = await self.jellyfin_client.get_item_by_id(jellyfin_id, user_id)
                if not item_data:
                    logger.error(f"Could not fetch item data for {jellyfin_id}")
                    return
                
                # Extract media info
                media_info = self.jellyfin_client.extract_media_info(item_data)
                is_watched = media_info.get("is_watched", False)
                item_name = media_info.get("title", "Unknown")
                item_type = media_info.get("media_type", "Unknown")
                
                # Check if this is a special episode from Jellyfin data
                if media_info.get("season_number") == 0 and settings.ignore_special_episodes:
                    logger.info(f"Ignoring special episode (season 0) from Jellyfin data for {item_name}")
                    return
            
            logger.info(f"Processing watched status change: {item_name} ({item_type}) -> watched={is_watched}")
            
            async with get_db_session() as db:
                # For webhook data that includes the info, create a simplified media_info
                if "is_watched" in webhook_data:
                    # Normalize media type from Jellyfin to our enum
                    jellyfin_item_type = webhook_data.get("item_type", "Unknown")
                    normalized_media_type = self._normalize_media_type(jellyfin_item_type)
                    
                    media_info = {
                        "jellyfin_id": jellyfin_id,
                        "title": webhook_data.get("item_name", "Unknown"),
                        "media_type": normalized_media_type,
                        "is_watched": is_watched,
                        "user_id": user_id,
                        # Add series/season info for episodes
                        "series_id": webhook_data.get("series_id"),
                        "series_name": webhook_data.get("series_name"),
                        "season_number": webhook_data.get("season_number"),
                        "episode_number": webhook_data.get("episode_number"),
                        "parent_id": webhook_data.get("series_id")  # For episodes, parent is series
                    }
                    
                    logger.info(f"Created media_info: {media_info}")
                
                # Get or create media item
                logger.info(f"🔄 Step 1: Creating/updating media item in database...")
                try:
                    media_item = await self._get_or_create_media_item_from_jellyfin(db, media_info)
                    if not media_item:
                        logger.error(f"❌ Could not create media item for {jellyfin_id}")
                        return
                    
                    logger.info(f"✅ Media item created: ID={media_item.id}, Type={media_item.media_type}")
                    
                    # Attach webhook data for improved series/movie matching
                    if "is_watched" in webhook_data:
                        media_item._webhook_data = webhook_data
                    
                    # Compare BEFORE updating database
                    old_status = media_item.is_watched
                    logger.info(f"📊 Status comparison - DB: {old_status}, Jellyfin: {is_watched}")
                    
                    # Check if status actually changed (unless force_sync is True)
                    if old_status == is_watched and not force_sync:
                        logger.info(f"⚠️ No status change detected (both are {is_watched}), skipping sync")
                        return
                    elif old_status == is_watched and force_sync:
                        logger.info(f"🔧 Force sync enabled - processing despite no status change ({is_watched})")
                    else:
                        logger.info(f"🔄 Status change detected: {old_status} → {is_watched}")
                    
                    # Update database
                    media_item.is_watched = is_watched
                    await db.commit()
                    logger.info(f"✅ Database updated: {old_status} → {is_watched}")
                    
                except Exception as db_error:
                    logger.error(f"❌ Database error: {db_error}")
                    logger.exception("Database operation failed:")
                    await db.rollback()
                    return
                
                # One-way unmonitor mode: If media was marked unwatched or replayed,
                # do NOT re-monitor in Sonarr/Radarr. Once unmonitored, keep unmonitored.
                if not is_watched and not force_sync:
                    logger.info(f"⏭️ Skipping re-monitor for '{item_name}' (one-way unmonitor mode: unwatched events ignored)")
                    return

                # Sync logic based on Jellyfin status:
                # is_watched = true → unmonitor (don't need to download anymore)
                should_monitor = False
                action_text = "unmonitor"
                service_name = 'Sonarr' if media_item.media_type in [MediaType.EPISODE, MediaType.SERIES, MediaType.SEASON] else 'Radarr'
                
                logger.info(f"🎯 Action: {action_text} '{item_name}' in {service_name}")
                logger.info(f"💡 Jellyfin watched={is_watched} → Sonarr/Radarr monitor={should_monitor}")
                
                # Sync with appropriate service
                try:
                    await self._sync_with_service(db, media_item, should_monitor, force_sync, is_retry, original_log_id)
                    logger.info(f"✅ Sync completed successfully for '{media_item.title}'")
                except Exception as sync_error:
                    logger.error(f"❌ Sync failed for '{media_item.title}': {sync_error}")
                    logger.exception("Full sync error traceback:")
                
        except Exception as e:
            logger.error(f"Error processing watched status change: {e}")
    
    async def _get_or_create_media_item(self, db: AsyncSession, jellyfin_id: str) -> Optional[MediaItem]:
        """Get existing media item or create new one from Jellyfin."""
        # First try to get existing item
        stmt = select(MediaItem).where(MediaItem.jellyfin_id == jellyfin_id)
        result = await db.execute(stmt)
        media_item = result.scalar_one_or_none()
        
        if media_item:
            return media_item
        
        # If not found, fetch from Jellyfin and create
        item_data = await self.jellyfin_client.get_item_by_id(jellyfin_id)
        if not item_data:
            return None
        
        media_info = self.jellyfin_client.extract_media_info(item_data)
        return await self._get_or_create_media_item_from_jellyfin(db, media_info)
    
    async def _get_or_create_media_item_from_jellyfin(
        self, 
        db: AsyncSession, 
        media_info: Dict[str, Any]
    ) -> Optional[MediaItem]:
        """Create or update media item from Jellyfin data."""
        jellyfin_id = media_info.get("jellyfin_id")
        if not jellyfin_id:
            logger.error("❌ No jellyfin_id provided in media_info")
            return None
        
        try:
            logger.debug(f"🔍 Looking for existing media item with ID: {jellyfin_id}")
            # Check if item already exists
            stmt = select(MediaItem).where(MediaItem.jellyfin_id == jellyfin_id)
            result = await db.execute(stmt)
            media_item = result.scalar_one_or_none()
            
            if media_item:
                logger.debug(f"📝 Updating existing media item: {media_item.title}")
                # Update existing item (but keep current is_watched for comparison)
                media_item.title = media_info.get("title", media_item.title)
                # DON'T update is_watched here - we'll do it after comparison
                media_item.parent_id = media_info.get("parent_id", media_item.parent_id)
                media_item.series_name = media_info.get("series_name", media_item.series_name)
                media_item.season_number = media_info.get("season_number", media_item.season_number)
                media_item.episode_number = media_info.get("episode_number", media_item.episode_number)
                logger.debug(f"✅ Updated metadata, current watched status: {media_item.is_watched}")
            else:
                logger.debug(f"➕ Creating new media item: {media_info.get('title')}")
                # Create new item - always start as unwatched to detect change
                media_item = MediaItem(
                    jellyfin_id=jellyfin_id,
                    title=media_info.get("title", ""),
                    media_type=media_info.get("media_type", "unknown"),
                    is_watched=False,  # Always start as unwatched to detect first change
                    parent_id=media_info.get("parent_id"),
                    series_name=media_info.get("series_name"),
                    season_number=media_info.get("season_number"),
                    episode_number=media_info.get("episode_number")
                )
                db.add(media_item)
                logger.debug(f"✅ Created: {media_item.title} (type: {media_item.media_type}, starts as unwatched)")
            
            # Don't commit here - let the caller handle transactions
            await db.flush()  # Just flush to get the ID
            await db.refresh(media_item)  # Refresh to get the ID
            
            return media_item
            
        except Exception as e:
            logger.error(f"❌ Error in _get_or_create_media_item_from_jellyfin: {e}")
            logger.exception("Full traceback:")
            raise
    
    async def _sync_with_service(
        self, 
        db: AsyncSession, 
        media_item: MediaItem, 
        should_monitor: bool, 
        force: bool = False,
        is_retry: bool = False,
        original_log_id: Optional[int] = None
    ) -> None:
        """Generic sync method for both Sonarr and Radarr."""
        service_name = "Sonarr" if media_item.media_type in [MediaType.EPISODE, MediaType.SERIES, MediaType.SEASON] else "Radarr"
        service_client = self.sonarr_client if service_name == "Sonarr" else self.radarr_client
        mapping_model = SonarrMapping if service_name == "Sonarr" else RadarrMapping
        
        try:
            if should_monitor and not force:
                logger.info(f"⏭️ Skipping monitor action for '{media_item.title}' (one-way unmonitor mode)")
                return

            logger.info(f"Starting {service_name} sync for: {media_item.title} (Type: {media_item.media_type})")
            logger.info(f"Should monitor: {should_monitor}, Force: {force}")

            # Check for existing mapping
            stmt = select(mapping_model).where(mapping_model.media_item_id == media_item.id)
            result = await db.execute(stmt)
            mapping = result.scalar_one_or_none()
            logger.info(f"Existing {service_name} mapping: {mapping is not None}")

            if not mapping:
                if service_name == "Sonarr":
                    webhook_context = getattr(media_item, '_webhook_data', None)
                    series = await self._find_sonarr_series(media_item, webhook_context)
                    if not series:
                        logger.warning(f"Could not find series in Sonarr for {media_item.title}")
                        return
                    
                    logger.info(f"Found series in Sonarr: {series.get('title', 'Unknown')} (ID: {series.get('id')})")
                    
                    mapping = SonarrMapping(
                        media_item_id=media_item.id,
                        sonarr_series_id=series["id"]
                    )
                    
                    if media_item.media_type == MediaType.EPISODE:
                        episode = await self.sonarr_client.search_episode(
                            series["id"], 
                            media_item.season_number, 
                            media_item.episode_number
                        )
                        if episode:
                            mapping.sonarr_episode_id = episode["id"]
                            mapping.sonarr_season_number = media_item.season_number
                else: # Radarr
                    webhook_context = getattr(media_item, '_webhook_data', None)
                    movie = await self._find_radarr_movie(media_item, webhook_context)
                    if not movie:
                        logger.warning(f"Could not find movie in Radarr for {media_item.title}. Skipping sync.")
                        return
                    
                    logger.info(f"Found movie in Radarr: {movie.get('title', 'Unknown')} (ID: {movie.get('id')})")
                    
                    mapping = RadarrMapping(
                        media_item_id=media_item.id,
                        radarr_movie_id=movie["id"]
                    )
                
                db.add(mapping)
                await db.commit()

            if not mapping:
                logger.error(f"No {service_name} mapping available for {media_item.title}")
                return

            # Create or update sync log
            sync_log = await self._update_sync_log(db, media_item, should_monitor, service_name.lower(), is_retry, original_log_id)

            # Perform the sync
            success = False
            if service_name == "Sonarr":
                if media_item.media_type == MediaType.EPISODE and mapping.sonarr_episode_id:
                    success = await self.sonarr_client.update_episode_monitoring(
                        mapping.sonarr_episode_id, should_monitor
                    )
                elif media_item.media_type == MediaType.SEASON:
                    success = await self.sonarr_client.update_season_monitoring(
                        mapping.sonarr_series_id, 
                        mapping.sonarr_season_number,
                        should_monitor
                    )
                elif media_item.media_type == MediaType.SERIES:
                    success = await self.sonarr_client.update_series_monitoring(
                        mapping.sonarr_series_id, should_monitor
                    )
            else: # Radarr
                success = await self.radarr_client.update_movie_monitoring(
                    mapping.radarr_movie_id, should_monitor
                )

            # Update sync log and mapping
            sync_log.status = SyncStatus.COMPLETED if success else SyncStatus.FAILED
            sync_log.external_id = str(mapping.sonarr_series_id if service_name == "Sonarr" else mapping.radarr_movie_id)
            if not success:
                sync_log.error_message = "Failed to update monitoring status"
            
            mapping.is_monitored = should_monitor
            await db.commit()

            action_name = "monitored" if should_monitor else "unmonitored"
            logger.info(f"Successfully {action_name} {media_item.title} in {service_name}")

        except Exception as e:
            logger.error(f"Error syncing with {service_name}: {e}")
            logger.exception(f"Full {service_name} sync error traceback:")
            if 'sync_log' in locals() and sync_log:
                sync_log.status = SyncStatus.FAILED
                sync_log.error_message = str(e)
                await db.commit()

    async def _update_sync_log(self, db: AsyncSession, media_item: MediaItem, should_monitor: bool, service: str, is_retry: bool, original_log_id: Optional[int]) -> SyncLog:
        if is_retry and original_log_id:
            existing_log_result = await db.execute(
                select(SyncLog).where(SyncLog.id == original_log_id)
            )
            sync_log = existing_log_result.scalar_one_or_none()
            if sync_log:
                sync_log.status = SyncStatus.PROCESSING
                sync_log.error_message = None
                sync_log.updated_at = datetime.utcnow()
                logger.info(f"🔄 Updated existing sync log ID {original_log_id} for retry")
                return sync_log
            else:
                logger.warning(f"Original log ID {original_log_id} not found, creating new log")
        
        sync_log = SyncLog(
            media_item_id=media_item.id,
            series_name=media_item.series_name or media_item.title,
            action=SyncAction.MONITOR if should_monitor else SyncAction.UNMONITOR,
            status=SyncStatus.PROCESSING,
            service=service
        )
        db.add(sync_log)
        await db.commit()
        return sync_log
    
    async def _find_sonarr_series(self, media_item: MediaItem, webhook_data: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        """Find corresponding series in Sonarr using Jellyfin Provider IDs as primary source."""
        # Always prioritize series information for episodes
        series_title = media_item.title
        series_year = None
        existing_ids = {}
        
        if webhook_data:
            # ALWAYS use SeriesName for episodes, not episode title
            if media_item.media_type == MediaType.EPISODE:
                series_title = webhook_data.get("series_name") or webhook_data.get("SeriesName", media_item.title)
                logger.info(f"📺 Episode detected - using SeriesName: '{series_title}' instead of episode title: '{media_item.title}'")
                # For episodes, year should be series year, not episode year
                series_year = webhook_data.get("SeriesPremiereDate")
                if series_year:
                    # Extract year from date like "2025-07-06"
                    try:
                        series_year = int(series_year.split('-')[0])
                    except:
                        series_year = None
            else:
                series_title = webhook_data.get("item_name", media_item.title)
                series_year = webhook_data.get("year")
            
            # Extract Provider IDs from Jellyfin webhook (primary source of truth)
            existing_ids = {
                "tvdb_id": webhook_data.get("Provider_tvdb") or webhook_data.get("tvdb_id"),
                "imdb_id": webhook_data.get("Provider_imdb") or webhook_data.get("imdb_id"),
                "tmdb_id": webhook_data.get("Provider_tmdb") or webhook_data.get("tmdb_id")
            }
            
            logger.info(f"🎯 Jellyfin metadata - Series: '{series_title}', Year: {series_year}")
            logger.info(f"📋 Provider IDs - TVDB: {existing_ids.get('tvdb_id')}, IMDB: {existing_ids.get('imdb_id')}, TMDB: {existing_ids.get('tmdb_id')}")
        
        # For episodes without webhook data, try to get series info from Jellyfin
        if media_item.media_type == MediaType.EPISODE and media_item.parent_id and not webhook_data:
            series_data = await self.jellyfin_client.get_item_by_id(media_item.parent_id)
            if series_data:
                series_info = self.jellyfin_client.extract_media_info(series_data)
                series_title = series_info.get("title", series_title)
                series_year = series_info.get("year")
        
        # PRIORITY 1: Direct search with Jellyfin Provider IDs (most reliable)
        if any(existing_ids.values()):
            logger.info(f"🎯 PRIORITY 1: Using Jellyfin Provider IDs for direct matching")
            direct_match = await self.sonarr_client.find_series_by_jellyfin_metadata(
                title=series_title,
                year=series_year,
                tvdb_id=existing_ids.get("tvdb_id"),
                imdb_id=existing_ids.get("imdb_id")
            )
            
            if direct_match:
                logger.info(f"✅ DIRECT MATCH found using Jellyfin Provider IDs: {direct_match.get('title')}")
                return direct_match
            else:
                logger.warning(f"⚠️ No direct match found with Provider IDs, trying external API enhancement")
        
        # PRIORITY 2: Enhance with external API if Provider IDs failed or missing
        if settings.use_external_api and self.external_api_client:
            logger.info(f"🌐 PRIORITY 2: Enhancing with external API for '{series_title}'")
            
            # OMDb API only works with IMDB IDs, so only pass IMDB ID to external API
            external_api_ids = {}
            if existing_ids.get("imdb_id"):
                external_api_ids["imdb_id"] = existing_ids["imdb_id"]
                logger.info(f"🎯 Passing only IMDB ID to external API: {existing_ids['imdb_id']}")
            else:
                logger.info(f"⚠️ No IMDB ID available for external API enhancement")
            
            external_match = await self.external_api_client.find_best_match(
                title=series_title,
                media_type="series",
                year=series_year,
                existing_ids=external_api_ids if external_api_ids else None
            )
            
            if external_match:
                logger.info(f"✅ External API enhanced match: {external_match['title']} - IMDB: {external_match.get('imdb_id')}")
                
                # Try again with enhanced metadata
                enhanced_result = await self.sonarr_client.find_series_by_jellyfin_metadata(
                    title=external_match["title"],
                    year=external_match.get("year"),
                    tvdb_id=external_match.get("tvdb_id"),
                    imdb_id=external_match.get("imdb_id")
                )
                
                if enhanced_result:
                    return enhanced_result
                
                # If enhanced search failed, use the external API title for final fallback
                logger.info(f"🔄 PRIORITY 3: Final fallback with external API title (non-localized): {external_match['title']}")
                return await self.sonarr_client.find_series_by_jellyfin_metadata(
                    title=external_match["title"],
                    year=external_match.get("year"),
                    tvdb_id=None,
                    imdb_id=None
                )
            else:
                logger.warning(f"❌ External API found no enhancement for '{series_title}'")
        
        # PRIORITY 3: Final fallback - title-only search with original Jellyfin title
        logger.info(f"🔄 PRIORITY 3: Final fallback - title-only search for: {series_title}")
        return await self.sonarr_client.find_series_by_jellyfin_metadata(
            title=series_title,
            year=series_year,
            tvdb_id=None,
            imdb_id=None
        )
    
    async def _find_radarr_movie(self, media_item: MediaItem, webhook_data: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        """Find corresponding movie in Radarr using Jellyfin Provider IDs as primary source."""
        # Extract metadata from webhook if available
        movie_title = media_item.title
        movie_year = None
        existing_ids = {}
        
        if webhook_data:
            movie_title = webhook_data.get("item_name", media_item.title)
            movie_year = webhook_data.get("year")
            
            # Extract Provider IDs from Jellyfin webhook (primary source of truth)
            existing_ids = {
                "tmdb_id": webhook_data.get("Provider_tmdb") or webhook_data.get("tmdb_id"),
                "imdb_id": webhook_data.get("Provider_imdb") or webhook_data.get("imdb_id")
            }
            
            logger.info(f"🎬 Jellyfin metadata - Movie: '{movie_title}', Year: {movie_year}")
            logger.info(f"📋 Provider IDs - TMDB: {existing_ids.get('tmdb_id')}, IMDB: {existing_ids.get('imdb_id')}")
        else:
            # Fallback: Get additional metadata from Jellyfin
            item_data = await self.jellyfin_client.get_item_by_id(media_item.jellyfin_id)
            if item_data:
                media_info = self.jellyfin_client.extract_media_info(item_data)
                movie_title = media_info.get("title", movie_title)
                movie_year = media_info.get("year")
        
        # PRIORITY 1: Direct search with Jellyfin Provider IDs (most reliable)
        if any(existing_ids.values()):
            logger.info(f"🎯 PRIORITY 1: Using Jellyfin Provider IDs for direct matching. TMDB: {existing_ids.get('tmdb_id')}, IMDB: {existing_ids.get('imdb_id')}")
            direct_match = await self.radarr_client.find_movie_by_jellyfin_metadata(
                title=movie_title,
                year=movie_year,
                tmdb_id=existing_ids.get("tmdb_id"),
                imdb_id=existing_ids.get("imdb_id")
            )
            
            if direct_match:
                logger.info(f"✅ DIRECT MATCH found using Jellyfin Provider IDs: {direct_match.get('title')}")
                return direct_match
            else:
                logger.warning(f"⚠️ No direct match found with Provider IDs for '{movie_title}', trying external API enhancement")
        
        # PRIORITY 2: Enhance with external API if Provider IDs failed or missing
        if settings.use_external_api and self.external_api_client:
            logger.info(f"🌐 PRIORITY 2: Enhancing with external API for '{movie_title}'")
            
            # OMDb API only works with IMDB IDs, so only pass IMDB ID to external API
            external_api_ids = {}
            if existing_ids.get("imdb_id"):
                external_api_ids["imdb_id"] = existing_ids["imdb_id"]
                logger.info(f"🎯 Passing only IMDB ID to external API: {existing_ids['imdb_id']}")
            else:
                logger.info(f"⚠️ No IMDB ID available for external API enhancement")
            
            external_match = await self.external_api_client.find_best_match(
                title=movie_title,
                media_type="movie",
                year=movie_year,
                existing_ids=external_api_ids if external_api_ids else None
            )
            
            if external_match:
                logger.info(f"✅ External API enhanced match: {external_match['title']} - IMDB: {external_match.get('imdb_id')}")
                
                # Try again with enhanced metadata
                enhanced_result = await self.radarr_client.find_movie_by_jellyfin_metadata(
                    title=external_match["title"],
                    year=external_match.get("year"),
                    tmdb_id=external_match.get("tmdb_id"),
                    imdb_id=external_match.get("imdb_id")
                )
                
                if enhanced_result:
                    return enhanced_result
                
                # If enhanced search failed, use the external API title for final fallback
                logger.info(f"🔄 PRIORITY 3: Final fallback with external API title (non-localized): {external_match['title']}")
                return await self.radarr_client.find_movie_by_jellyfin_metadata(
                    title=external_match["title"],
                    year=external_match.get("year"),
                    tmdb_id=None,
                    imdb_id=None
                )
            else:
                logger.warning(f"❌ External API found no enhancement for '{movie_title}'")
        
        # PRIORITY 3: Final fallback - title-only search with original Jellyfin title
        logger.info(f"🔄 PRIORITY 3: Final fallback - title-only search for: {movie_title}")
        return await self.radarr_client.find_movie_by_jellyfin_metadata(
            title=movie_title,
            year=movie_year,
            tmdb_id=None,
            imdb_id=None
        )
    
    def _cleanup_processing_cache(self) -> None:
        """Clean up old entries from processing cache."""
        cutoff = datetime.utcnow() - timedelta(minutes=10)
        expired_keys = [
            key for key, timestamp in self._processing_cache.items()
            if timestamp < cutoff
        ]
        
        for key in expired_keys:
            self._processing_cache.pop(key, None)