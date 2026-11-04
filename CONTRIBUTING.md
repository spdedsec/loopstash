# Contributing to LOOPSTASH

Thank you for your interest in contributing to **LOOPSTASH**! We welcome community contributions aimed at enhancing stability, reliability, and platform support.

---

## 🛠️ Development Setup

1. **Fork and Clone**:
   ```bash
   git clone https://github.com/spdedsec/loopstash.git ~/loopstash
   cd ~/loopstash
   ```

2. **Create a Virtual Environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install Editable Package with Development Dependencies**:
   ```bash
   pip install --upgrade pip
   pip install -e ".[dev]"
   ```

---

## 🧪 Testing Guidelines

We enforce automated tests for all functionality and bug fixes. Before submitting a pull request:

```bash
# Run full pytest suite
pytest -v

# Run Ruff linter
ruff check .

# Run Ruff formatter check
ruff format --check .
```

All 55+ tests must pass without errors, and Ruff must report 0 linting or formatting violations.

### Writing New Tests
- Place tests in the `tests/` directory following the naming pattern `test_<component>.py`.
- Mock external network requests and Instagram interactions; do NOT invoke live network calls in unit tests.
- Use pytest fixtures provided in `tests/conftest.py` (such as `test_db`, `test_storage`, `tmp_dir`).

---

## 📐 Coding Conventions

- **Python Version**: Target Python 3.11+. Use modern typing annotations (`list[str]`, `dict[str, Any]`, `Optional[int]`).
- **Formatting**: Line length is capped at 100 characters. Code must be formatted with `ruff format`.
- **Subprocess Safety**: Always pass arguments as lists (`[binary, "--flag"]`), never use `shell=True`.
- **Security & Privacy**:
  - Never log passwords, cookies, or sensitive tokens.
  - No telemetry, analytics, or background telemetry workers.
- **Error Handling**: Custom exceptions must inherit from `BackendError` or standard Python exceptions and classify retriable vs non-retriable failures.

---

## 🔀 Pull Request Process

1. Create a feature branch from `master` (`git checkout -b feature/my-new-feature`).
2. Write clean, self-documenting code with tests.
3. Commit with concise, descriptive commit messages.
4. Verify tests and linting (`pytest` and `ruff check`).
5. Open a Pull Request on GitHub with a clear description of the problem and proposed solution.
