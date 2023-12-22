# Docker environment for local development in devcontainer

FROM ubuntu:jammy-20231128

RUN apt-get update --fix-missing && \
    apt-get upgrade -y && \
    apt-get install -y --fix-missing \
        curl \
        unzip \
        software-properties-common \
        vim \
        git \
        python3-pip

COPY --from=docker.io/bitnami/kubectl:1.28.5            /opt/bitnami/kubectl/bin/kubectl /usr/local/bin/kubectl
COPY --from=registry.k8s.io/kustomize/kustomize:v5.3.0  /app/kustomize                   /usr/local/bin/kustomize
COPY --from=ghcr.io/kyverno/kyverno-cli:v1.10.7         /ko-app/kubectl-kyverno          /usr/local/bin/kyverno
COPY --from=docker.io/alpine/helm:3.13.3                /usr/bin/helm                    /usr/local/bin/helm
COPY --from=ghcr.io/fluxcd/flux-cli:v2.2.1              /usr/local/bin/flux              /usr/local/bin/flux

COPY requirements.txt /src/
RUN pip3 install -r /src/requirements.txt

SHELL ["/bin/bash", "-c"]
