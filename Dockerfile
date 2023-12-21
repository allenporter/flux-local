FROM docker.io/alpine/helm:3.13.3 as helm
FROM docker.io/bitnami/kubectl:1.28.5 as kubectl
FROM ghcr.io/fluxcd/flux-cli:v2.2.1 as flux
FROM registry.k8s.io/kustomize/kustomize:v5.3.0 as kustomize

FROM python:3.10-alpine as base

RUN apk add --no-cache ca-certificates git

WORKDIR /app
COPY requirements.txt /requirements.txt
COPY flux_local/ ./flux_local
COPY setup.py .
COPY setup.cfg .

RUN pip install --no-cache-dir -r /requirements.txt
RUN pip install -e .

COPY --from=flux       /usr/local/bin/flux              /usr/local/bin/flux
COPY --from=helm       /usr/bin/helm                    /usr/local/bin/helm
COPY --from=kubectl    /opt/bitnami/kubectl/bin/kubectl /usr/local/bin/kubectl
COPY --from=kustomize  /app/kustomize                   /usr/local/bin/kustomize

CMD ["/usr/local/bin/flux-local"]
