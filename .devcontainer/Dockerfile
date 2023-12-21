# Docker environment for local development in devcontainer
FROM docker.io/alpine/helm:3.13.3 as helm
FROM docker.io/bitnami/kubectl:1.28.5 as kubectl
FROM ghcr.io/fluxcd/flux-cli:v2.2.1 as flux
FROM ghcr.io/kyverno/kyverno-cli:v1.10.7 as kyverno
FROM registry.k8s.io/kustomize/kustomize:v5.3.0 as kustomize

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

COPY . /src/
WORKDIR /src/
RUN pip3 install -r /src/requirements.txt
RUN pip3 install -e /src/

COPY --from=ghcr.io/fluxcd/flux-cli:v2.2.1              /usr/local/bin/flux              /usr/local/bin/flux
COPY --from=docker.io/alpine/helm:3.13.3                /usr/bin/helm                    /usr/local/bin/helm
COPY --from=docker.io/bitnami/kubectl:1.28.5            /opt/bitnami/kubectl/bin/kubectl /usr/local/bin/kubectl
COPY --from=registry.k8s.io/kustomize/kustomize:v5.3.0  /app/kustomize                   /usr/local/bin/kustomize
COPY --from=ghcr.io/kyverno/kyverno-cli:v1.10.7         /ko-app/kubectl-kyverno          /usr/local/bin/kyverno

SHELL ["/bin/bash", "-c"]
