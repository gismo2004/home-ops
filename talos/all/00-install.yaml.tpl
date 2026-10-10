---
apiVersion: v1alpha1
kind: UnattendedInstallConfig
provisioning:
  diskSelector:
    {{- if .Node.Data.installDisk }}
    match: disk.dev_path == "{{ .Node.Data.installDisk }}"
    {{- else }}
    match: disk.serial == "{{ .Node.Data.installDiskSerial }}"
    {{- end }}
