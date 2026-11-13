# The hitsave-deploy container: every 5 minutes it runs scripts/deploy/deploy-poll.sh from the
# live checkout, which deploys main once its checks pass. See docs/deploy-on-merge.md.
FROM alpine:3.20
RUN apk add --no-cache bash coreutils curl docker-cli docker-cli-buildx docker-cli-compose \
    flock git python3
COPY run-loop.sh /usr/local/bin/run-loop.sh
ENTRYPOINT ["/usr/local/bin/run-loop.sh"]
