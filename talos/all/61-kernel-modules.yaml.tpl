{{- if .Node.Data.kernelModules }}
machine:
  kernel:
    modules:
      {{- range .Node.Data.kernelModules }}
      - name: {{ .name }}
        {{- with index . "parameters" }}
        parameters:
          {{- range . }}
          - {{ . }}
          {{- end }}
        {{- end }}
      {{- end }}
{{- end }}
