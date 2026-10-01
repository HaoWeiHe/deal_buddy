# xiaohongshu-mcp pointed at rednote.com (小紅書國際版).
# Accounts registered with a non-Chinese phone number are sent from xiaohongshu.com to rednote.com after
# login, so the upstream MCP (which only knows www.xiaohongshu.com) never sees them as logged in.
# This rebuilds the same upstream code with the web domain swapped and drops it into the upstream image.
FROM golang:1.24 AS builder
ARG MCP_REF=main
RUN git clone --depth 1 --branch ${MCP_REF} https://github.com/xpzouying/xiaohongshu-mcp /src
WORKDIR /src
RUN grep -rl 'www\.xiaohongshu\.com' --include='*.go' . | xargs sed -i 's#www\.xiaohongshu\.com#www.rednote.com#g' \
    && CGO_ENABLED=0 go build -ldflags="-s -w" -o /out/app .

FROM xpzouying/xiaohongshu-mcp
COPY --from=builder /out/app /app/app
