# Caddy is the only thing listening on the public network, so it is built with exactly the two
# modules this deployment needs: the web application firewall and, for deployments that use it,
# the DNS challenge provider.
FROM docker.io/library/caddy:2.11.4-builder AS build

RUN xcaddy build v2.11.4 \
    --with github.com/corazawaf/coraza-caddy/v2@v2.6.1 \
    --with github.com/caddy-dns/googleclouddns@v1.1.0


# The rules the firewall enforces, fetched once and checked against the digests recorded here.
FROM docker.io/library/debian:trixie-slim AS rules

ARG CRS_VERSION=4.29.0
ARG CRS_SHA256=1aa1c5c8fc29e532d35293bcea36bf72de61db8f6ed4716a0f91ab14552b7fed
ARG CORAZA_VERSION=3.7.0
ARG CORAZA_CONF_SHA256=fea02902c81b2b9691746e08b934dea5ded6382aa7b561c31b9edef00cc5c956

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && rm --recursive --force /var/lib/apt/lists/* \
    && mkdir --parents /rules \
    && curl --fail --silent --show-error --location --output /tmp/crs.tar.gz \
        "https://github.com/coreruleset/coreruleset/releases/download/v${CRS_VERSION}/coreruleset-${CRS_VERSION}-minimal.tar.gz" \
    && echo "${CRS_SHA256}  /tmp/crs.tar.gz" | sha256sum --check --strict \
    && tar --extract --gzip --file /tmp/crs.tar.gz --directory /rules --strip-components=1 \
    && mv /rules/crs-setup.conf.example /rules/crs-setup.conf \
    && curl --fail --silent --show-error --location --output /rules/coraza.conf \
        "https://raw.githubusercontent.com/corazawaf/coraza/v${CORAZA_VERSION}/coraza.conf-recommended" \
    && echo "${CORAZA_CONF_SHA256}  /rules/coraza.conf" | sha256sum --check --strict


FROM docker.io/library/debian:trixie-slim

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm --recursive --force /var/lib/apt/lists/* \
    && useradd --uid 10003 --user-group --no-create-home --shell /usr/sbin/nologin caddy \
    && mkdir --parents /var/log/caddy /var/lib/caddy /config \
    && chown caddy:caddy /var/log/caddy /var/lib/caddy /config

COPY --from=build /usr/bin/caddy /usr/bin/caddy
COPY --from=rules /rules /etc/caddy/coraza
COPY containers/caddy/coraza/panel.conf /etc/caddy/coraza/panel.conf
COPY containers/caddy/Caddyfile /etc/caddy/Caddyfile

# Certificates and the WAF's audit log are the only state Caddy keeps.
ENV XDG_DATA_HOME=/var/lib/caddy \
    XDG_CONFIG_HOME=/config

USER caddy
EXPOSE 80 443
ENTRYPOINT ["caddy"]
CMD ["run", "--config", "/etc/caddy/Caddyfile"]
