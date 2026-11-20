# Changelog

## [0.1.2] - 2026-11-20

- Market data: every data tool reads Tauric's current data API, so prices, quotes, financials,
  ownership, news, digests, macro series and rates work again.
- Market data: when Tauric says an answer is incomplete, the tool says so with Tauric's reason;
  values Tauric marks provisional are named.
- Market data: a value that is empty for a reason Tauric gives is stated with that reason, and
  with "ask again later" when it may come later (such as a next earnings date not announced
  yet).
- Market data: a period the source did not publish (such as CPI for October 2025) is named as
  not published, not as incomplete.
- Prices: the hourly interval is `1h`; long daily histories are read a year at a time.
- Session sync reads your account's sessions and storage as Tauric serves them, so restore and
  `trading-r1 sync pull` work again.
- Requests to Tauric name the app and its version.
- Terminal: it does not attach to a running backend of another version; it says which version
  runs and how to update it.
- Terminal: the Model and Mode pickers mark the current item with a check mark.
- Terminal: warnings from Python libraries go to the log file instead of over the screen.
- Web search: a search whose queries all come back empty is run once more after a short pause.

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
