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

# renovate: datasource=github-releases depName=kubernetes-sigs/kustomize
ARG KUSTOMIZE_VERSION=v5.0.3
RUN cd /usr/local/bin/ && \
    curl -OL https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2F${KUSTOMIZE_VERSION}/kustomize_${KUSTOMIZE_VERSION}_linux_amd64.tar.gz && \
    tar xf kustomize_${KUSTOMIZE_VERSION}_linux_amd64.tar.gz && \
    chmod +x kustomize
RUN kustomize version

# renovate: datasource=github-releases depName=helm/helm
ARG HELM_CLI_VERSION=v3.12.1
RUN mkdir -p /src && \
    cd /src && \
    curl -OL https://get.helm.sh/helm-${HELM_CLI_VERSION}-linux-amd64.tar.gz && \
    tar xf helm-${HELM_CLI_VERSION}-linux-amd64.tar.gz && \
    cp linux-amd64/helm /usr/local/bin/helm && \
    rm -fr /src
RUN helm version

# renovate: datasource=github-releases depName=kyverno/kyverno
ARG KYVERNO_VERSION=v1.10.0
RUN mkdir -p /src && \
    cd /src && \
    curl -OL https://github.com/kyverno/kyverno/releases/download/${KYVERNO_VERSION}/kyverno-cli_${KYVERNO_VERSION}_linux_x86_64.tar.gz && \
    tar xf kyverno-cli_${KYVERNO_VERSION}_linux_x86_64.tar.gz && \
    cp kyverno /usr/local/bin/kyverno && \
    chmod +x /usr/local/bin/kyverno && \
    rm -fr /src
RUN kyverno version

# renovate: datasource=github-releases depName=fluxcd/flux2 extractVersion=^v(?<version>.+)$
ARG FLUX_CLI_VERSION=2.1.2
RUN mkdir -p /src && \
    cd /src && \
    curl -OL https://github.com/fluxcd/flux2/releases/download/v${FLUX_CLI_VERSION}/flux_${FLUX_CLI_VERSION}_linux_amd64.tar.gz && \
    tar xf flux_${FLUX_CLI_VERSION}_linux_amd64.tar.gz && \
    cp flux /usr/local/bin/flux && \
    rm -fr /src
RUN flux version --client

COPY . /src/
WORKDIR /src/
RUN pip3 install -r /src/requirements.txt

SHELL ["/bin/bash", "-c"]
