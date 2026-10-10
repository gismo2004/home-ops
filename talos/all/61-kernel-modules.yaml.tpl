{{- range .Node.Data.kernelModules }}
---
apiVersion: v1alpha1
kind: KernelModuleConfig
name: {{ .name }}
{{- with index . "parameters" }}
parameters:
  {{- range . }}
  - {{ . }}
  {{- end }}
{{- end }}
{{- end }}
