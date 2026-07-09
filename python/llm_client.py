def build_llm_client(provider, api_key, base_url="", *, is_async: bool):
    if not api_key and provider != "openai_compatible":
        return None
    try:
        if provider == "anthropic":
            import anthropic

            return anthropic.AsyncAnthropic(api_key=api_key) if is_async else anthropic.Anthropic(api_key=api_key)
        import openai

        kwargs = {"api_key": api_key or "ollama"}
        if provider == "openai_compatible" and base_url:
            kwargs["base_url"] = base_url.strip()
        return openai.AsyncOpenAI(**kwargs) if is_async else openai.OpenAI(**kwargs)
    except Exception as e:
        prefix = "" if is_async else "sync "
        print(f"[CLIENT] Failed to build {prefix}{provider} client: {e}", flush=True)
        return None
