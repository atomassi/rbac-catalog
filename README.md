# Azure RBAC Catalog

[![Build](https://github.com/atomassi/azurerbac-builtinroles/actions/workflows/build.yml/badge.svg)](https://github.com/atomassi/azurerbac-builtinroles/actions/workflows/build.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/atomassi/56d5c381b196c9c18db9fedbecb79220/raw/coverage.json&cacheSeconds=3600)](https://github.com/atomassi/azurerbac-builtinroles/actions/workflows/build.yml)
[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

**Live site:** [rbac-catalog.dev](https://rbac-catalog.dev/recommend?ai=1)

A comprehensive catalog and monitoring tool for [Azure built-in RBAC roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles). Browse roles, explore their permissions, track changes over time, find least-privilege roles based on operation requirements, and get AI-powered role recommendations.

## Features

- **Role Catalog** — Browse all 800+ Azure built-in roles with full permission details
- **Operation Explorer** — Search 15,000+ resource provider operations
- **Change Tracking** — Monitor when Microsoft adds, modifies, or deprecates roles
- **AI Role Recommender** — Describe what you need in natural language, get least-privilege role suggestions
- **Diff Viewer** — See exactly what changed between role versions
- **8 AI Recommendation Modes** — From fast keyword matching to LLM-powered semantic understanding

## Tech Stack

| Layer | Technologies |
|-------|-------------|
| **Backend** | Python 3.12+, FastAPI, SQLAlchemy, Pydantic |
| **Frontend** | Jinja2 templates, Tailwind CSS, Alpine.js |
| **Database** | PostgreSQL |
| **AI/ML** | Ollama, sentence-transformers, ColBERT, Qwen (fine-tuned) |
| **Hosting** | Azure App Service, Cloudflare CDN |
| **CI/CD** | GitHub Actions, Azure Container Registry, Docker |

## Infrastructure & Costs

The site runs on Azure with Cloudflare CDN.

### Architecture

```mermaid
flowchart TD
    USER((👥  Users)):::user
    GH[GitHub Actions]:::github
    CDN[Cloudflare]:::cdn
    
    subgraph AZ ["<span style='font-size:22px;font-weight:bold'>Azure</span>"]
        CR[(Container<br/>Registry)]:::azure
        AS[App Service]:::azure
        PG[(PostgreSQL)]:::db
        AI[App Insights]:::monitor
        OL[Ollama VM<br/>B2a v2]:::ollama
        GPU[GPU VM<br/>NVIDIA A10]:::gpu
    end
    
    USER -->|requests| CDN
    CDN -->|proxy| AS
    GH -->|push image| CR
    CR -->|deploy| AS
    AS <-->|queries/ingestion| PG
    AS -->|telemetry| AI
    AS -->|inference| OL
    GPU -.->|models| OL
    
    classDef user fill:#FFC107,color:#000,stroke:#FFA000,stroke-width:2px
    classDef github fill:#24292e,color:#fff,stroke:#1a1e22,stroke-width:2px
    classDef cdn fill:#F6821F,color:#fff,stroke:#d4700f,stroke-width:2px
    classDef azure fill:#0078D4,color:#fff,stroke:#005a9e,stroke-width:2px
    classDef db fill:#336791,color:#fff,stroke:#264d73,stroke-width:2px
    classDef ollama fill:#412991,color:#fff,stroke:#301d6b,stroke-width:2px
    classDef gpu fill:#76B900,color:#fff,stroke:#5a8c00,stroke-width:2px
    classDef monitor fill:#68217A,color:#fff,stroke:#4e185c,stroke-width:2px
    
    style AZ fill:#E6F2FA,stroke:#0078D4,stroke-width:2px,rx:10
```

### Cost

| Service | $/month |
|---------|--------:|
| Cloudflare | $0 |
| App Service | ~$45 |
| PostgreSQL | ~$13 |
| Container Registry | ~$5 |
| App Insights | ~$5 |
| Ollama VM (inference) | ~$32 |
| GPU VM (training/finetuning) | on-demand (~1$/hour) |
| **Total** | **~$100** |

## AI Recommendation Modes

> [!NOTE]
> This project was created for my personal learning and for experimenting with different recommendation modes. The recommendation modes are experimental and may produce inaccurate results. I plan to continue improving and fine-tuning them.

The AI Role Recommender supports **8 different modes**, each with different speed/accuracy trade-offs:

| Mode | Description | Requires |
|------|-------------|----------|
| **TF-IDF** | Enhanced TF-IDF + BM25 keyword matching | CPU only |
| **Semantic** | Pure sentence embedding similarity | Embeddings |
| **ColBERT** | Token-level late interaction for precise matching | ColBERT index |
| **Cross-Encoder** | Bi-encoder retrieval + neural reranking | Embeddings |
| **LLM** | Fine-tuned Qwen model direct inference | Ollama |
| **RAG** | Retrieval-Augmented Generation with LLM reranking | Embeddings + Ollama |
| **HyDE** | Hypothetical document generation + semantic search | Embeddings + Ollama |
| **Hybrid** | Multi-stage: TF-IDF → Embeddings → LLM pipeline | All components |

## LLM Fine-Tuning

The LLM mode uses a fine-tuned [Qwen2.5-0.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct) model trained specifically for Azure RBAC role matching.

### Approach

Fine-tuning was done using [Unsloth](https://github.com/unslothai/unsloth) for efficient LoRA training on consumer hardware. The model takes natural language queries like "I need to read blob storage" and outputs structured JSON with role recommendations and confidence scores.

## Knowledge Base

Each role is converted into a searchable `document_text` combining:
- **Role name & description** — From Azure's Role Definition API
- **Action keywords** — Tokenized from expanded permissions (e.g., `Microsoft.Compute/virtualMachines/powerOff/action` → `virtualmachines poweroff action`)
- **Curated patterns** — Human-written query examples (e.g., "read blob storage")

```mermaid
flowchart LR
    subgraph Sources["<b>Sources</b>"]
        API["Role API"]
        Ops["Permissions API"]
        Patterns["Curated patterns"]
    end
    
    API -->|permissions/wildcards| Effective
    Ops -->|all operations| Effective
    Effective[Compute effective<br/>permissions] -->|expanded ops| Tokenize[Tokenize]
    Tokenize -->|keywords| DocText
    Patterns -->|search phrases| DocText
    
    DocText["document_text"]
    
    style Sources fill:#E6F2FA,stroke:#0078D4
    style DocText fill:#E8F5E9,stroke:#4CAF50
```

| Engine | How it uses `document_text` |
|--------|---------------------------|
| **TF-IDF** | BM25 keyword matching |
| **Semantic** | Embeds into vectors, cosine similarity |
| **ColBERT** | Token-level MaxSim matching |
| **LLM** | Doesn't use it—fine-tuned model predicts directly |

## Testing

```bash
# Unit tests (runs on Python 3.12-3.13)
pytest tests/ -q --cov=azurerbac

# E2E tests
npm ci
npx playwright install chromium
npm run test:e2e

# Smoke tests (pre-production validation)
pip install httpx
python scripts/smoke_tests.py --url https://your-staging-url.azurewebsites.net
```

## Deployment

Deployments use a **staging-first approach** with automatic promotion:

```mermaid
flowchart LR
    A[Push to release] --> B[Build Docker image]
    B --> C[Push to ACR]
    C --> D[Deploy to staging slot]
    D --> E[Run smoke tests]
    E -->|Pass| F[Swap to production]
    E -->|Fail| G[Abort deployment]
```

1. **Build** — GitHub Actions builds a versioned Docker image
2. **Push to ACR** — The image is pushed to Azure Container Registry
3. **Deploy to Staging** — The image is deployed to the App Service staging slot
4. **Smoke Tests** — Automated tests validate pages, APIs, and AI recommender endpoints
5. **Slot Swap** — If all tests pass, staging is swapped to production

This ensures every production deployment is validated before users see it.

> See [Azure App Service staging slots](https://learn.microsoft.com/en-us/azure/app-service/deploy-staging-slots) for more details.

## Project Structure

```
azurerbac/
├── airecommender/   # AI recommendation engines (8 modes)
├── azure/           # Azure SDK integration (roles, operations)
├── backgroundjobs/  # Scheduled tasks and background workers
├── cache/           # Caching layer for roles and operations
├── core/            # Database models and utilities
├── matching/        # Operation-to-role matching for least-privilege role composition
├── telemetry/       # Application Insights integration
└── web/             # FastAPI app, routes, templates

scripts/             # Deployment and smoke test scripts
tests/               # Unit tests
e2e/                 # Playwright end-to-end tests
```

## References

- **TF-IDF/BM25**: Robertson & Zaragoza, [The Probabilistic Relevance Framework: BM25 and Beyond](https://www.staff.city.ac.uk/~sbrp622/papers/foundations_bm25_review.pdf) (2009)
- **Sentence-BERT**: Reimers & Gurevych, [Sentence Embeddings using Siamese BERT-Networks](https://arxiv.org/abs/1908.10084) (2019)
- **ColBERT**: Khattab & Zaharia, [Efficient and Effective Passage Search via Contextualized Late Interaction](https://arxiv.org/abs/2004.12832) (2020)
- **Cross-Encoder**: Humeau et al., [Poly-encoders: Architectures and Pre-training Strategies](https://arxiv.org/abs/1905.01969) (2019)
- **Qwen**: Bai et al., [Qwen Technical Report](https://arxiv.org/abs/2309.16609) (2023)
- **RAG**: Lewis et al., [Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks](https://arxiv.org/abs/2005.11401) (2020)
- **HyDE**: Gao et al., [Precise Zero-Shot Dense Retrieval without Relevance Labels](https://arxiv.org/abs/2212.10496) (2022)