{{/*
Expand the name of the chart.
*/}}
{{- define "ai-ecommerce.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "ai-ecommerce.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "ai-ecommerce.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to all resources.
*/}}
{{- define "ai-ecommerce.labels" -}}
helm.sh/chart: {{ include "ai-ecommerce.chart" . }}
{{ include "ai-ecommerce.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: ai-ecommerce-platform
{{- end }}

{{/*
Selector labels — used in matchLabels and Service selectors.
*/}}
{{- define "ai-ecommerce.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ai-ecommerce.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Service-specific labels for a given microservice.
Usage: include "ai-ecommerce.serviceLabels" (dict "root" . "serviceName" "user-service")
*/}}
{{- define "ai-ecommerce.serviceLabels" -}}
helm.sh/chart: {{ include "ai-ecommerce.chart" .root }}
app.kubernetes.io/name: {{ .serviceName }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- if .root.Chart.AppVersion }}
app.kubernetes.io/version: {{ .root.Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
app.kubernetes.io/part-of: ai-ecommerce-platform
app: {{ .serviceName }}
{{- end }}

{{/*
Service-specific selector labels.
Usage: include "ai-ecommerce.serviceSelectorLabels" (dict "serviceName" "user-service")
*/}}
{{- define "ai-ecommerce.serviceSelectorLabels" -}}
app: {{ .serviceName }}
{{- end }}

{{/*
Create the image reference for a service.
Usage: include "ai-ecommerce.serviceImage" (dict "root" . "svc" .Values.services.userService "name" "user-service")
*/}}
{{- define "ai-ecommerce.serviceImage" -}}
{{- $registry := .root.Values.global.image.registry -}}
{{- $repo := .svc.image.repository -}}
{{- $tag := coalesce .svc.image.tag .root.Values.global.image.tag "latest" -}}
{{- printf "%s/%s:%s" $registry $repo $tag }}
{{- end }}

{{/*
Create a standard Deployment for a microservice.
Usage: include "ai-ecommerce.serviceDeployment" (dict "root" . "svc" .Values.services.userService "name" "user-service")
*/}}
{{- define "ai-ecommerce.serviceDeployment" -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .name }}
  namespace: {{ .root.Values.global.namespace }}
  labels:
    {{- include "ai-ecommerce.serviceLabels" (dict "root" .root "serviceName" .name) | nindent 4 }}
spec:
  replicas: {{ .svc.replicaCount }}
  selector:
    matchLabels:
      {{- include "ai-ecommerce.serviceSelectorLabels" (dict "serviceName" .name) | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "ai-ecommerce.serviceSelectorLabels" (dict "serviceName" .name) | nindent 8 }}
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/path: "/metrics"
        prometheus.io/port: {{ .svc.port | quote }}
        checksum/config: {{ include (print .root.Template.BasePath "/configmap.yaml") .root | sha256sum }}
        checksum/secret: {{ include (print .root.Template.BasePath "/secrets.yaml") .root | sha256sum }}
    spec:
      {{- with .root.Values.global.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      containers:
        - name: {{ .name }}
          image: {{ include "ai-ecommerce.serviceImage" (dict "root" .root "svc" .svc "name" .name) }}
          imagePullPolicy: {{ .root.Values.global.image.pullPolicy }}
          ports:
            - name: http
              containerPort: {{ .svc.port }}
              protocol: TCP
          envFrom:
            - configMapRef:
                name: app-config
            - secretRef:
                name: app-secrets
          resources:
            {{- toYaml .svc.resources | nindent 12 }}
          readinessProbe:
            httpGet:
              path: /health
              port: {{ .svc.port }}
            initialDelaySeconds: 10
            periodSeconds: 5
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /health
              port: {{ .svc.port }}
            initialDelaySeconds: 30
            periodSeconds: 10
            failureThreshold: 3
          securityContext:
            allowPrivilegeEscalation: false
            runAsNonRoot: true
            runAsUser: 1000
      terminationGracePeriodSeconds: 30
{{- end }}

{{/*
Create a standard Service for a microservice.
Usage: include "ai-ecommerce.serviceService" (dict "root" . "svc" .Values.services.userService "name" "user-service")
*/}}
{{- define "ai-ecommerce.serviceService" -}}
apiVersion: v1
kind: Service
metadata:
  name: {{ .name }}
  namespace: {{ .root.Values.global.namespace }}
  labels:
    {{- include "ai-ecommerce.serviceLabels" (dict "root" .root "serviceName" .name) | nindent 4 }}
spec:
  selector:
    {{- include "ai-ecommerce.serviceSelectorLabels" (dict "serviceName" .name) | nindent 4 }}
  ports:
    - name: http
      port: {{ .svc.port }}
      targetPort: {{ .svc.port }}
      protocol: TCP
  type: ClusterIP
{{- end }}

{{/*
Create an HPA for a microservice.
Usage: include "ai-ecommerce.serviceHPA" (dict "root" . "svc" .Values.services.userService "name" "user-service")
*/}}
{{- define "ai-ecommerce.serviceHPA" -}}
{{- if .svc.autoscaling.enabled }}
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ .name }}-hpa
  namespace: {{ .root.Values.global.namespace }}
  labels:
    {{- include "ai-ecommerce.serviceLabels" (dict "root" .root "serviceName" .name) | nindent 4 }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ .name }}
  minReplicas: {{ .svc.autoscaling.minReplicas }}
  maxReplicas: {{ .svc.autoscaling.maxReplicas }}
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {{ .svc.autoscaling.targetCPUUtilizationPercentage }}
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 300
    scaleUp:
      stabilizationWindowSeconds: 60
{{- end }}
{{- end }}
