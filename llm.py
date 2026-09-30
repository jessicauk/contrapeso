"""Selección de proveedor para LangChain/LangGraph y CrewAI."""
import os
from pathlib import Path

from dotenv import load_dotenv


def provider(agent=None):
    """Proveedor efectivo de un agente (o el global si no tiene uno propio)."""
    load_dotenv(Path(__file__).with_name('.env'))
    prefix = f'{agent.upper()}_' if agent else ''
    return os.getenv(prefix + 'LLM_PROVIDER', os.getenv('LLM_PROVIDER', 'ollama')).strip().lower()


def settings(agent=None):
    load_dotenv(Path(__file__).with_name('.env'))
    prefix = f'{agent.upper()}_' if agent else ''
    prov = provider(agent)
    model = os.getenv(prefix + 'MODELO') if agent else None
    if prov == 'ollama':
        return dict(model=model or os.getenv('OLLAMA_MODEL', 'llama3.2:3b'),
                    base_url=os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434/v1'),
                    api_key='ollama')
    if prov == 'kimi':
        key = os.getenv('MOONSHOT_API_KEY', '').strip()
        if not key:
            raise ValueError('Configura MOONSHOT_API_KEY en .env para usar Kimi.')
        return dict(model=model or os.getenv('KIMI_MODEL', 'kimi-k3'),
                    base_url=os.getenv('KIMI_BASE_URL', 'https://api.moonshot.ai/v1'),
                    api_key=key)
    raise ValueError('LLM_PROVIDER debe ser ollama o kimi.')


def langchain_llm():
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(**settings(), timeout=180, max_retries=0)


def crewai_llm(agent=None, max_tokens=None, timeout=180):
    """LLM para CrewAI. max_tokens y timeout son límites duros por llamada (OWASP LLM10)."""
    from crewai import LLM
    config = settings(agent)
    config['model'] = 'openai/' + config['model']
    return LLM(**config, max_tokens=max_tokens, timeout=timeout, max_retries=0)


if __name__ == '__main__':
    import sys
    prompt = ' '.join(sys.argv[1:]) or 'Responde brevemente en español: ¿estás listo?'
    print(langchain_llm().invoke(prompt).content)