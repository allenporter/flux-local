# Docker environment for local development in devcontainer
FROM docker.io/alpine/helm:3.13.3 as helm
FROM docker.io/bitnami/kubectl:1.28.5 as kubectl
FROM ghcr.io/fluxcd/flux-cli:v2.2.1 as flux
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

# renovate: datasource=github-releases depName=kyverno/kyverno
ARG KYVERNO_VERSION=v1.11.1
RUN mkdir -p /src && \
    cd /src && \
    curl -OL https://github.com/kyverno/kyverno/releases/download/${KYVERNO_VERSION}/kyverno-cli_${KYVERNO_VERSION}_linux_x86_64.tar.gz && \
    tar xf kyverno-cli_${KYVERNO_VERSION}_linux_x86_64.tar.gz && \
    cp kyverno /usr/local/bin/kyverno && \
    chmod +x /usr/local/bin/kyverno && \
    rm -fr /src
RUN kyverno version

COPY . /src/
WORKDIR /src/
RUN pip3 install -r /src/requirements.txt
RUN pip3 install -e /src/

COPY --from=flux       /usr/local/bin/flux              /usr/local/bin/flux
COPY --from=helm       /usr/bin/helm                    /usr/local/bin/helm
COPY --from=kubectl    /opt/bitnami/kubectl/bin/kubectl /usr/local/bin/kubectl
COPY --from=kustomize  /app/kustomize                   /usr/local/bin/kustomize

SHELL ["/bin/bash", "-c"]
