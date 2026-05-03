# Self-Hosted Firecrawl

## Deployment

```bash
# start firecrawl
docker compose up -d
# check crawl api
curl -X POST http://localhost:3002/v1/crawl \
    -H 'Content-Type: application/json' \
    -d '{
      "url": "https://firecrawl.dev"
    }'
curl -X GET http://localhost:3002/v1/crawl/019ded41-0eb3-73aa-ba77-dcd578f51b2f

# watch logs
docker logs firecrawl-api-1
```

## Test crawl
```bash
curl -X POST http://localhost:3002/v1/crawl \
    -H 'Content-Type: application/json' \
    -d '{
      "url": "https://arxiv.org/pdf/2604.27117"
    }'

curl -X GET http://localhost:3002/v1/crawl/019ded3c-5ef6-777d-ba5a-743c024b76c3
```

## Setup Hermes

```bash
# set FIRECRAWL_API_URL
hermes config set FIRECRAWL_API_URL http://host.docker.internal:3002
hermes
# use verbose mode to check details
/verbose
# input message
Please help me review the abstract of this arXiv paper: https://arxiv.org/pdf/2604.27117
```