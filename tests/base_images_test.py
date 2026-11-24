"""The base-image helper the daily rebuild workflow calls (scripts/base_images.py)."""

from __future__ import annotations

import base_images


def test_lists_arg_default_and_literal_bases_skipping_stage_names() -> None:
    dockerfile = """\
ARG BUILD_IMAGE=dhi.io/python:3.14-alpine3.24-sfw-dev
ARG RUNTIME_IMAGE=dhi.io/python:3.14-alpine3.24
ARG UNUSED=example.com/not-a-base:1
FROM denoland/deno:alpine-2.8.3 AS deno_src
RUN true
FROM ${BUILD_IMAGE} AS builder
FROM builder AS tested
FROM --platform=$TARGETPLATFORM $RUNTIME_IMAGE
COPY --from=deno_src /bin/deno /usr/local/bin/deno
"""
    assert base_images.base_images(dockerfile) == [
        "denoland/deno:alpine-2.8.3",
        "dhi.io/python:3.14-alpine3.24-sfw-dev",
        "dhi.io/python:3.14-alpine3.24",
    ]


_REGISTRY_TAGS = [
    "3.9-alpine3.24",
    "3.14-alpine3.23",
    "3.14-alpine3.24",
    "3.14-alpine3.24-sfw-dev",
    "3.14-alpine3.25",
    "3.14-alpine3.25-dev",
    "3.14-alpine3.25-sfw-dev",
    "3.14.1-alpine3.25",
    "3.14-debian13",
    "3.15-alpine3.23",
    "3.15-alpine3.24",
    "3.15-rc-alpine3.25",
    "latest",
]


def test_newer_tags_compare_versions_numerically_within_the_variant() -> None:
    assert base_images.newer_tags("3.14-alpine3.24", _REGISTRY_TAGS) == [
        "3.14-alpine3.25",
        "3.15-alpine3.24",
    ]


def test_newer_tags_keep_to_the_current_variant() -> None:
    assert base_images.newer_tags("3.14-alpine3.24-sfw-dev", _REGISTRY_TAGS) == [
        "3.14-alpine3.25-sfw-dev",
    ]
