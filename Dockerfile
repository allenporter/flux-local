FROM python:3.13-alpine as base

RUN apk add --no-cache ca-certificates git

WORKDIR /app
COPY flux_local/ ./flux_local
COPY pyproject.toml .

RUN pip install -e .

COPY --from=ghcr.io/fluxcd/flux-cli:v2.7.2  /usr/local/bin/flux  /usr/local/bin/flux
COPY --from=docker.io/alpine/helm:3.19.0    /usr/bin/helm        /usr/local/bin/helm

# renovate: datasource=github-releases depName=kubernetes-sigs/kustomize
ARG KUSTOMIZE_VERSION=v5.7.1
ARG TARGETARCH
RUN wget -qO- \
  "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize/${KUSTOMIZE_VERSION}/kustomize_${KUSTOMIZE_VERSION}_linux_${TARGETARCH}.tar.gz" \
  | tar xz -C /usr/local/bin kustomize

USER 1001
ENTRYPOINT ["/usr/local/bin/flux-local"]
