# Changelog

## [0.1.1] - 2026-11-09

- Reports: the PDF is set in Noto Sans, with Noto Sans SC and KR, so it draws Chinese, Japanese
  and Korean text and common symbols such as currency signs, fractions and arrows.
- Export: the terminal and `trading-r1 export` both say when a report cannot be a PDF, and offer the
  HTML page.
- Formulas are written in Markdown rather than LaTeX.
- Telegram: a report is titled by its job or Trading-R1 and the day, and a table reads as one line
  per row.

## [0.1.0] - 2026-11-04

First official release of Trading-R1, the terminal for financial research and analysis by
[Tauric Research](https://tauric.ai/).

- Ask a question, and the agent pulls market data, reads the web, runs the numbers and writes a
  cited report.
- 68 tools and 17 workflow skills: company, market and macro data; valuation, risk, portfolio,
  statistics, fixed income and derivatives.
- Models: Tauric, OpenAI, Anthropic, Gemini, vLLM and Ollama.
- Reports as PDF, Markdown or an HTML page, sent to Telegram, email or a folder.
- Scheduled reports, a background service for Linux and macOS, and Telegram replies.
- Session sync to your Tauric account, on by default, and `SKILL.md` workflows of your own.
