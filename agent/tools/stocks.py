"""Stock price lookup via yfinance (free, no API key)."""


def get_stock_price(symbol: str) -> str:
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info = ticker.info
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        if price is None:
            return f"Error: No price data for {symbol}"
        name = info.get("shortName", symbol)
        change = info.get("regularMarketChange", 0)
        pct = info.get("regularMarketChangePercent", 0)
        high = info.get("dayHigh", "N/A")
        low = info.get("dayLow", "N/A")
        volume = info.get("volume", "N/A")
        return (
            f"{name} ({symbol})\n"
            f"  Price:  ${price:.2f}\n"
            f"  Change: {change:+.2f} ({pct:+.2f}%)\n"
            f"  High:   ${high}  Low: ${low}\n"
            f"  Volume: {volume}"
        )
    except Exception as e:
        return f"Error: {e}"
