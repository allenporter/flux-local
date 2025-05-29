FROM python:3.13-alpine as base

RUN apk add --no-cache ca-certificates git

WORKDIR /app
COPY flux_local/ ./flux_local
COPY setup.py .
COPY setup.cfg .

RUN pip install -e .

COPY --from=ghcr.io/fluxcd/flux-cli:v2.6.0              /usr/local/bin/flux              /usr/local/bin/flux
COPY --from=docker.io/alpine/helm:3.18.1                /usr/bin/helm                    /usr/local/bin/helm
COPY --from=registry.k8s.io/kustomize/kustomize:v5.6.0  /app/kustomize                   /usr/local/bin/kustomize

USER 1001
ENTRYPOINT ["/usr/local/bin/flux-local"]
