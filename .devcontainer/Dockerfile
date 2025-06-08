# Docker environment for local development in devcontainer

FROM ubuntu:noble-20250529

RUN apt-get update --fix-missing && \
    apt-get upgrade -y && \
    apt-get install -y --fix-missing \
        curl \
        unzip \
        software-properties-common \
        vim \
        git \
        python3-pip

COPY --from=docker.io/bitnami/kubectl:1.33.1            /opt/bitnami/kubectl/bin/kubectl /usr/local/bin/kubectl
COPY --from=registry.k8s.io/kustomize/kustomize:v5.6.0  /app/kustomize                   /usr/local/bin/kustomize
COPY --from=docker.io/alpine/helm:3.18.2                /usr/bin/helm                    /usr/local/bin/helm
COPY --from=ghcr.io/fluxcd/flux-cli:v2.6.1              /usr/local/bin/flux              /usr/local/bin/flux

SHELL ["/bin/bash", "-c"]
