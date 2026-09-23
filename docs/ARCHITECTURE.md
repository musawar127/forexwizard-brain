# Target Architecture

```text
                    FOREXWIZARD AI MARKET BRAIN

                        Next.js Web UI
                              |
                         FastAPI Gateway
                              |
       ------------------------------------------------
       |                 |               |            |
 Market Data        Market Engine     Research     AI Answerer
       |                 |               |            |
 Provider layer     Deterministic     Web/RSS/API     RAG
       |             calculations         |            |
 Twelve Data        Structure/ATR     Source DB     Memory
       |                 |               |            |
       ---------------- PostgreSQL / pgvector ---------
                              |
                            Redis
```

## Design principles

1. Correct data before predictions.
2. External provider secrets stay on the server.
3. The browser never calls paid/limited providers directly.
4. All market calculations are deterministic Python code first.
5. LLMs explain and synthesize; they do not invent prices or indicators.
6. BUY/SELL/WAIT must have reasons for and against.
7. WAIT is the default when evidence or data quality is insufficient.
8. Every prediction is versioned and evaluated later.
9. Learning means updating memory/statistics and promoting only validated strategy versions — not uncontrolled self-modifying code.
10. Real-money execution is a separate future risk-controlled module.
