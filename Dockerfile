FROM python:3.14-alpine as base

ARG UID=1001
ARG GID=1001
RUN addgroup -g $GID flux-local && adduser -G flux-local -D -u $UID flux-local

RUN apk add --no-cache ca-certificates git

WORKDIR /app
COPY flux_local/ ./flux_local
COPY pyproject.toml .

RUN pip install -e .

COPY --from=ghcr.io/fluxcd/flux-cli:v2.8.8  /usr/local/bin/flux  /usr/local/bin/flux
COPY --from=docker.io/alpine/helm:4.2.2     /usr/bin/helm        /usr/local/bin/helm

# renovate: datasource=github-releases depName=kubernetes-sigs/kustomize
ARG KUSTOMIZE_VERSION=v5.7.1
ARG TARGETARCH
RUN wget -qO- \
  "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize/${KUSTOMIZE_VERSION}/kustomize_${KUSTOMIZE_VERSION}_linux_${TARGETARCH}.tar.gz" \
  | tar xz -C /usr/local/bin kustomize

USER $UID
ENTRYPOINT ["/usr/local/bin/flux-local"]
