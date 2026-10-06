from fastapi import FastAPI

from crypto_signal_engine import __version__

app = FastAPI(
    title="Crypto Signal Engine",
    version=__version__,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "version": __version__,
    }
