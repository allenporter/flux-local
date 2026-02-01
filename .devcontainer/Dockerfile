# Docker environment for local development in devcontainer

FROM mcr.microsoft.com/devcontainers/python:3.14-bookworm

RUN apt-get update --fix-missing && \
    apt-get upgrade -y && \
    apt-get install -y --fix-missing \
        curl \
        unzip \
        vim \
        git

COPY --from=registry.k8s.io/kubectl:v1.35.0             /bin/kubectl                     /usr/local/bin/kubectl
COPY --from=registry.k8s.io/kustomize/kustomize:v5.8.0  /app/kustomize                   /usr/local/bin/kustomize
COPY --from=docker.io/alpine/helm:4.1.0                /usr/bin/helm                    /usr/local/bin/helm
COPY --from=ghcr.io/fluxcd/flux-cli:v2.7.5              /usr/local/bin/flux              /usr/local/bin/flux

SHELL ["/bin/bash", "-c"]
