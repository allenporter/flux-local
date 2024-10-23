# Docker environment for local development in devcontainer

FROM ubuntu:jammy-20240808

RUN apt-get update --fix-missing && \
    apt-get upgrade -y && \
    apt-get install -y --fix-missing \
        curl \
        unzip \
        software-properties-common \
        vim \
        git \
        python3-pip

COPY --from=docker.io/bitnami/kubectl:1.31.2            /opt/bitnami/kubectl/bin/kubectl /usr/local/bin/kubectl
COPY --from=registry.k8s.io/kustomize/kustomize:v5.4.3  /app/kustomize                   /usr/local/bin/kustomize
COPY --from=ghcr.io/kyverno/kyverno-cli:v1.12.6         /ko-app/kubectl-kyverno          /usr/local/bin/kyverno
COPY --from=docker.io/alpine/helm:3.16.2                /usr/bin/helm                    /usr/local/bin/helm
COPY --from=ghcr.io/fluxcd/flux-cli:v2.4.0              /usr/local/bin/flux              /usr/local/bin/flux

COPY requirements_dev.txt /src/
RUN pip3 install -r /src/requirements_dev.txt

SHELL ["/bin/bash", "-c"]
