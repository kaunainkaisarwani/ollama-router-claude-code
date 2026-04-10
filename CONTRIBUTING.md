# Contributing to Ollama Router

Thanks for your interest in contributing! 🎉

## Getting Started

1. **Fork** the repository
2. **Clone** your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/ollama-router.git
   cd ollama-router
   ```
3. **Install** in development mode:
   ```bash
   pip install -e ".[dev]"
   ```

## Development

### Project Structure

```
ollama-router/
├── router/
│   ├── __init__.py          # Package version
│   ├── cli.py               # CLI commands (Typer)
│   ├── config.py            # Configuration management
│   ├── gateway.py           # FastAPI gateway (Anthropic → Ollama translation)
│   ├── menu.py              # Interactive TUI menu
│   ├── proxy.py             # Proxy utilities
│   ├── rotation.py          # API rotation & failover logic
│   ├── session_manager.py   # Claude Code session management
│   └── utils.py             # Shared utilities & styling
├── pyproject.toml           # Project metadata & dependencies
├── README.md
├── LICENSE
└── CONTRIBUTING.md
```

### Running Tests

```bash
pytest
```

### Code Style

- Follow PEP 8 conventions
- Use type hints where possible
- Add docstrings to public functions

## Submitting Changes

1. Create a new branch: `git checkout -b feature/my-feature`
2. Make your changes
3. Test your changes: `pytest`
4. Commit with a clear message: `git commit -m "Add: my new feature"`
5. Push to your fork: `git push origin feature/my-feature`
6. Open a **Pull Request**

## Reporting Bugs

Open an [issue](https://github.com/ollama-router/ollama-router/issues) with:

- Steps to reproduce
- Expected vs actual behavior
- Your OS and Python version
- Output of `ollama-router --version`

## Feature Requests

Open an [issue](https://github.com/ollama-router/ollama-router/issues) describing:

- What you'd like to see
- Why it would be useful
- Any implementation ideas

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
