# Security

Echo-AI runs entirely on your computer. The local server listens only on `127.0.0.1`, rejects requests from other
hosts and other web pages (Host and Origin checks plus an app-only header), and serves a strict Content-Security-Policy.
The app uses the internet only to download models, the AI engine (Ollama) and the NVIDIA acceleration package, always
from the official sources and checked against their published SHA-256 checksums where they publish one.

If you find a security problem, please **do not open a public issue**. Report it privately through GitHub:
**Security → Report a vulnerability** on this repository. You will get an answer within a few days.
