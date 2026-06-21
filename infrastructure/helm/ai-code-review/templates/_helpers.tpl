{{/*
Expand the name of the chart.
*/}}
{{- define "ai-code-review.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Chart label value.
*/}}
{{- define "ai-code-review.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Target namespace.
*/}}
{{- define "ai-code-review.namespace" -}}
{{- .Values.namespace.name }}
{{- end }}

{{/*
ConfigMap resource name.
*/}}
{{- define "ai-code-review.configMapName" -}}
{{- .Values.configMap.name }}
{{- end }}

{{/*
Secret resource name.
*/}}
{{- define "ai-code-review.secretName" -}}
{{- .Values.secret.name }}
{{- end }}

{{/*
Helm-managed labels (excluding app.kubernetes.io/part-of).
*/}}
{{- define "ai-code-review.labels" -}}
helm.sh/chart: {{ include "ai-code-review.chart" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Component selector labels — name only, matching plain k8s manifests.
Usage: (dict "component" "api")
*/}}
{{- define "ai-code-review.componentSelectorLabels" -}}
app.kubernetes.io/name: {{ .component }}
{{- end }}

{{/*
Component metadata labels for Deployments, Services, and pod templates.
Usage: (dict "component" "api")
*/}}
{{- define "ai-code-review.componentLabels" -}}
app.kubernetes.io/name: {{ .component }}
app.kubernetes.io/part-of: code-review
{{- end }}

{{/*
Full image reference for a component values block.
Usage: include with .Values.api.image
*/}}
{{- define "ai-code-review.image" -}}
{{- printf "%s:%s" .repository .tag }}
{{- end }}

{{/*
Auto-built Redis URL when env.redisUrl is empty.
*/}}
{{- define "ai-code-review.redisUrl" -}}
{{- if .Values.env.redisUrl -}}
{{- .Values.env.redisUrl -}}
{{- else -}}
{{- printf "redis://%s:%v/0" .Values.redis.service.name .Values.redis.service.port -}}
{{- end -}}
{{- end }}

{{/*
Auto-built Chroma URL when env.chromaUrl is empty.
*/}}
{{- define "ai-code-review.chromaUrl" -}}
{{- if .Values.env.chromaUrl -}}
{{- .Values.env.chromaUrl -}}
{{- else -}}
{{- printf "http://%s:%v" .Values.chromadb.service.name .Values.chromadb.service.port -}}
{{- end -}}
{{- end }}

{{/*
Auto-built DATABASE_URL when secret.databaseUrl is empty.
*/}}
{{- define "ai-code-review.databaseUrl" -}}
{{- if .Values.secret.databaseUrl -}}
{{- .Values.secret.databaseUrl -}}
{{- else -}}
{{- printf "postgresql://%s:%s@%s:%v/%s" .Values.env.postgresUser .Values.secret.postgresPassword .Values.postgres.service.name .Values.postgres.service.port .Values.env.postgresDb -}}
{{- end -}}
{{- end }}
