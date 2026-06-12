# Docker environment for local development in devcontainer

FROM mcr.microsoft.com/devcontainers/python:3.14-bookworm

RUN apt-get update --fix-missing && \
    apt-get upgrade -y && \
    apt-get install -y --fix-missing \
        curl \
        unzip \
        vim \
        git

COPY --from=registry.k8s.io/kubectl:v1.36.1             /bin/kubectl                     /usr/local/bin/kubectl
COPY --from=registry.k8s.io/kustomize/kustomize:v5.8.1  /app/kustomize                   /usr/local/bin/kustomize
COPY --from=docker.io/alpine/helm:4.2.0                 /usr/bin/helm                    /usr/local/bin/helm
COPY --from=ghcr.io/fluxcd/flux-cli:v2.8.8              /usr/local/bin/flux              /usr/local/bin/flux
COPY --from=ghcr.io/astral-sh/uv:0.11.21                /uv /uvx                         /usr/local/bin/

SHELL ["/bin/bash", "-c"]
