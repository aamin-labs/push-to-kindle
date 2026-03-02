# Contributing to push-to-kindle

Thank you for your interest in contributing!

## Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/your-username/push-to-kindle.git
   cd push-to-kindle
   ```
3. Install dependencies:
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
4. Copy `.env.example` to `.env` and fill in your values:
   ```bash
   cp .env.example .env
   ```

## Making Changes

1. Create a branch for your change:
   ```bash
   git checkout -b feature/your-feature-name
   ```
2. Make your changes
3. Verify the script still works end-to-end with a test URL
4. Commit your changes with a clear message:
   ```bash
   git commit -m "Add feature: short description"
   ```
5. Push your branch and open a pull request:
   ```bash
   git push origin feature/your-feature-name
   ```

## Pull Request Guidelines

- Keep PRs focused — one feature or fix per PR
- Describe what your change does and why in the PR description
- If your change fixes a bug, reference the issue number

## Reporting Issues

Open an issue on GitHub with:
- A clear description of the problem
- Steps to reproduce it
- Expected vs. actual behavior
- Your environment (OS, Python version, etc.)

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
