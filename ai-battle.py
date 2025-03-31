"""AI model conversation manager with memory optimizations."""

import json
import os
import datetime
import base64
import sys
import time
import random
import logging
import re
import traceback
from typing import List, Dict, Optional, TypeVar, Any, Union
from dataclasses import dataclass
import io
import asyncio

# Local imports
from configuration import load_config, DiscussionConfig, detect_model_capabilities
from configdataclasses import FileConfig, DiscussionConfig
from arbiter_v4 import evaluate_conversations, VisualizationGenerator
from file_handler import ConversationMediaHandler
from model_clients import (
    BaseClient,
    OpenAIClient,
    ClaudeClient,
    GeminiClient,
    MLXClient,
    OllamaClient,
    PicoClient,
)
# move these into model_clients or separate everything..?
from lmstudio_client import LMStudioClient
from claude_reasoning_config import ClaudeReasoningConfig
from shared_resources import MemoryManager
from metrics_analyzer import analyze_conversations

T = TypeVar("T")
openai_api_key = os.getenv("OPENAI_API_KEY")
anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")


# Models to use in default mode
# these must match the model names below not necessary the exact actual model name
AI_MODEL = "gemini-2.5-pro-exp"
HUMAN_MODEL = "gemini-2.0-flash-thinking-exp"
DEFAULT_ROUNDS=2

# Set environment variables for these model names so arbiter can use them
os.environ["AI_MODEL"] = AI_MODEL
os.environ["HUMAN_MODEL"] = HUMAN_MODEL

CONFIG_PATH = "config.yaml"
TOKENS_PER_TURN = 2048
MAX_TOKENS = TOKENS_PER_TURN

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("ai_battle.log"), logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)

# Model templates for accessing different model versions with reasoning levels
OPENAI_MODELS = {
    # Base models (text-only with reasoning support)
    "o1": {"model": "o1", "reasoning_level": "auto", "multimodal": False},
    "o3": {"model": "o3", "reasoning_level": "auto", "multimodal": False},
    # O1 with reasoning levels (text-only)
    "o1-reasoning-high": {
        "model": "o1",
        "reasoning_level": "high",
        "multimodal": False,
    },
    "o1-reasoning-medium": {
        "model": "o1",
        "reasoning_level": "medium",
        "multimodal": False,
    },
    "o1-reasoning-low": {"model": "o1", "reasoning_level": "low", "multimodal": False},
    # O3 with reasoning levels (text-only)
    "o3-reasoning-high": {
        "model": "o3",
        "reasoning_level": "high",
        "multimodal": False,
    },
    "o3-reasoning-medium": {
        "model": "o3",
        "reasoning_level": "medium",
        "multimodal": False,
    },
    "o3-reasoning-low": {"model": "o3", "reasoning_level": "low", "multimodal": False},
    # Multimodal models without reasoning parameter
    "gpt-4o": {"model": "gpt-4o", "reasoning_level": None, "multimodal": True},
    "gpt-4o-mini": {
        "model": "gpt-4o-mini",
        "reasoning_level": None,
        "multimodal": True,
    },
}

CLAUDE_MODELS = {
    # Base models (newest versions)
    "claude": {
        "model": "claude-3-5-sonnet",
        "reasoning_level": None,
        "extended_thinking": False,
    },
    "sonnet": {
        "model": "claude-3-5-sonnet",
        "reasoning_level": None,
        "extended_thinking": False,
    },
    "haiku": {
        "model": "claude-3-5-haiku",
        "reasoning_level": None,
        "extended_thinking": False,
    },
    # Specific versions
    "claude-3-5-sonnet": {
        "model": "claude-3-5-sonnet",
        "reasoning_level": None,
        "extended_thinking": False,
    },
    "claude-3-5-haiku": {
        "model": "claude-3-5-haiku",
        "reasoning_level": None,
        "extended_thinking": False,
    },
    "claude-3-7": {
        "model": "claude-3-7-sonnet",
        "reasoning_level": "auto",
        "extended_thinking": False,
    },
    "claude-3-7-sonnet": {
        "model": "claude-3-7-sonnet",
        "reasoning_level": "auto",
        "extended_thinking": False,
    },
    # Claude 3.7 with reasoning levels
    "claude-3-7-reasoning": {
        "model": "claude-3-7-sonnet",
        "reasoning_level": "high",
        "extended_thinking": False,
    },
    "claude-3-7-reasoning-high": {
        "model": "claude-3-7-sonnet",
        "reasoning_level": "high",
        "extended_thinking": False,
    },
    "claude-3-7-reasoning-medium": {
        "model": "claude-3-7-sonnet",
        "reasoning_level": "medium",
        "extended_thinking": False,
    },
    "claude-3-7-reasoning-low": {
        "model": "claude-3-7-sonnet",
        "reasoning_level": "low",
        "extended_thinking": False,
    },
    "claude-3-7-reasoning-none": {
        "model": "claude-3-7-sonnet",
        "reasoning_level": "none",
        "extended_thinking": False,
    },
    # Claude 3.7 with extended thinking
    "claude-3-7-extended": {
        "model": "claude-3-7-sonnet",
        "reasoning_level": "high",
        "extended_thinking": True,
        "budget_tokens": 8000,
    },
    "claude-3-7-extended-deep": {
        "model": "claude-3-7-sonnet",
        "reasoning_level": "high",
        "extended_thinking": True,
        "budget_tokens": 16000,
    },
}


@dataclass
class ModelConfig:
    """Configuration for AI model parameters"""

    temperature: float = 0.8
    max_tokens: int = MAX_TOKENS 
    stop_sequences: List[str] = None
    seed: Optional[int] = random.randint(0, 1000)
    human_delay: float = 4.0


@dataclass
class ConversationManager:
    """Manages conversations between AI models with memory optimization."""

    def __init__(
        self,
        config: DiscussionConfig = None,
        domain: str = "General knowledge",
        human_delay: float = 4.0,
        mode: str = None,
        min_delay: float = 2,
        gemini_api_key: Optional[str] = None,
        claude_api_key: Optional[str] = None,
        openai_api_key: Optional[str] = None,
    ):
        self.config = config
        self.domain = config.goal if config else domain

        self.human_delay = human_delay
        self.mode = mode  # "human-aiai" or "ai-ai"
        self._media_handler = None  # Lazy initialization
        self.min_delay = min_delay
        self.conversation_history: List[Dict[str, str]] = []
        self.is_paused = False
        self.initial_prompt = domain
        self.rate_limit_lock = asyncio.Lock()
        self.last_request_time = 0

        # Store API keys
        self.openai_api_key = openai_api_key
        self.claude_api_key = claude_api_key
        self.gemini_api_key = gemini_api_key

        # Initialize empty client tracking
        self._initialized_clients = set()
        self.model_map = {}

    @property
    def media_handler(self):
        """Lazy initialization of media handler."""
        if self._media_handler is None:
            self._media_handler = ConversationMediaHandler(output_dir="processed_files")
        return self._media_handler

    def _get_client(self, model_name: str) -> Optional[BaseClient]:
        """
        Get an existing client instance or create a new one for the specified model.

        This method manages client instances, creating them on demand and caching them
        for reuse. It supports various model types including Claude, GPT, Gemini, MLX,
        Ollama, and Pico models.

        Args:
            model_name: Name of the model to get or create a client for

        Returns:
            Optional[BaseClient]: Client instance if successful, None if the model
                         is unknown or client creation fails
        """
        if model_name not in self._initialized_clients:
            try:
                # Handle Claude models using templates
                if model_name in CLAUDE_MODELS:
                    model_config = CLAUDE_MODELS[model_name]
                    client = ClaudeClient(
                        role=None,
                        api_key=self.claude_api_key,
                        mode=self.mode,
                        domain=self.domain,
                        model=model_config["model"],
                    )

                    # Set reasoning level if specified
                    if model_config["reasoning_level"] is not None:
                        client.reasoning_level = model_config["reasoning_level"]
                        logger.debug(
                            f"Set reasoning level to '{model_config['reasoning_level']}' for {model_name}"
                        )

                    # Set extended thinking if enabled
                    if model_config.get("extended_thinking", False):
                        budget_tokens = model_config.get("budget_tokens", None)
                        client.set_extended_thinking(True, budget_tokens)
                        logger.debug(
                            f"Enabled extended thinking with budget_tokens={budget_tokens} for {model_name}"
                        )

                # Handle OpenAI models using templates
                elif model_name in OPENAI_MODELS:
                    model_config = OPENAI_MODELS[model_name]
                    client = OpenAIClient(
                        api_key=self.openai_api_key,
                        role=None,
                        mode=self.mode,
                        domain=self.domain,
                        model=model_config["model"],
                    )

                    # Set reasoning level if specified
                    if model_config["reasoning_level"] is not None:
                        client.reasoning_level = model_config["reasoning_level"]
                        logger.debug(
                            f"Set reasoning level to '{model_config['reasoning_level']}' for {model_name}"
                        )

                # Handle other model types
                elif model_name == "gpt-4o":
                    client = OpenAIClient(
                        api_key=self.openai_api_key,
                        role=None,
                        mode=self.mode,
                        domain=self.domain,
                        model="chatgpt-latest",
                    )
                elif model_name == "gpt-4o-mini":
                    client = OpenAIClient(
                        api_key=self.openai_api_key,
                        role=None,
                        mode=self.mode,
                        domain=self.domain,
                        model="gpt-4o-mini",
                    )
                elif model_name == "gemini-2.0-flash-exp":
                    client = GeminiClient(
                        api_key=self.gemini_api_key,
                        role=None,
                        mode=self.mode,
                        domain=self.domain,
                        model="gemini-2.0-flash-exp",
                    )
                elif model_name == "gemini-2.0-flash-thinking-exp-01-21":
                    client = GeminiClient(
                        api_key=self.gemini_api_key,
                        role=None,
                        mode=self.mode,
                        domain=self.domain,
                        model="gemini-2.0-flash-thinking-exp-01-21",
                    )
                elif model_name == "gemini-2.0-flash-exp":
                    client = GeminiClient(
                        api_key=self.gemini_api_key,
                        role=None,
                        mode=self.mode,
                        domain=self.domain,
                        model="gemini-2.0-flash-exp",
                    )
                elif model_name == "gemini-2.0-flash-thinking-exp":
                    client = GeminiClient(
                        api_key=self.gemini_api_key,
                        role=None,
                        mode=self.mode,
                        domain=self.domain,
                        model="gemini-2.0-flash-thinking-exp",
                    )
                elif model_name == "gemini-2.0-pro":
                    client = GeminiClient(
                        api_key=self.gemini_api_key,
                        role=None,
                        mode=self.mode,
                        domain=self.domain,
                        model="gemini-2.0-pro-exp-02-05",
                    )
                elif model_name == "gemini-2.5-pro-exp":
                    client = GeminiClient(
                        api_key=self.gemini_api_key,
                        role=None,
                        mode=self.mode,
                        domain=self.domain,
                        model="gemini-2.5-pro-exp-03-25",
                    )
                elif model_name == "gemini-2.0-flash-lite":
                    client = GeminiClient(
                        api_key=self.gemini_api_key,
                        role=None,
                        mode=self.mode,
                        domain=self.domain,
                        model="gemini-2.0-flash-lite-preview-02-05",
                    )
                elif model_name == "mlx-qwq":
                    client = MLXClient(
                        mode=self.mode,
                        domain=self.domain,
                        model="mlx-community/Meta-Llama-3.1-8B-Instruct-abliterated-8bit",
                    )
                elif model_name == "mlx-llama-3.1-abb":
                    client = MLXClient(
                        mode=self.mode,
                        domain=self.domain,
                        model="mlx-community/Meta-Llama-3.1-8B-Instruct-abliterated-8bit",
                    )
                elif model_name == "lmstudio-QwQ-32B-8bit-MLX":
                     client = LMStudioClient(
                        mode=self.mode,
                        domain=self.domain,
                        model="mlx-community/QwQ-32B-8bit",
                    )
                elif model_name == "lmstudio-QwQ-32B-6bit-MLX":
                     client = LMStudioClient(
                        mode=self.mode,
                        domain=self.domain,
                        model="mlx-community/QwQ-32B-8bit",
                    )
                elif model_name == "lmstudio-gemma3-27b-bf16-MLX":
                     client = LMStudioClient(
                        mode=self.mode,
                        domain=self.domain,
                        model="mlx-community/gemma3-27b-it-bf16",
                    )
                elif model_name == "lmstudio-gemma3-27b-8bit-MLX":
                     client = LMStudioClient(
                        mode=self.mode,
                        domain=self.domain,
                        model="mlx-community/gemma3-27b-it-8bit",
                    )
                elif model_name == "lmstudio-phi4-bf16-MLX":
                     client = LMStudioClient(
                        mode=self.mode,
                        domain=self.domain,
                        model="mlx-community/phi-4-bf16",
                    )
                elif model_name == "lmstudio-gemma3-27b-unc-MLX":
                     client = LMStudioClient(
                        mode=self.mode,
                        domain=self.domain,
                        model="TheCluster/gemma-3-27b-it-uncensored-mlx",
                    )
                elif model_name == "pico-r1-14":
                    client = PicoClient(
                        mode=self.mode,
                        domain=self.domain,
                        model="DeepSeek-R1-Distill-Qwen-14B-abliterated-v2-Q4-mlx",
                    )
                elif model_name == "pico-r1-llama8b":
                    client = PicoClient(
                        mode=self.mode,
                        domain=self.domain,
                        model="DeepSeek-R1-Distill-Llama-8B-8bit-mlx",
                    )
                elif model_name == "pico-phi4-abb":
                    client = PicoClient(
                        mode=self.mode,
                        domain=self.domain,
                        model="phi-4-abliterated-3bit",
                    )
                elif model_name == "ollama":
                    client = OllamaClient(
                        mode=self.mode,
                        domain=self.domain,
                        model="mannix/llama3.1-8b-lexi:latest",
                    )
                elif model_name == "ollama-phi4":
                    client = OllamaClient(
                        mode=self.mode, domain=self.domain, model="phi4:latest"
                    )
                elif model_name == "ollama-lexi":
                    client = OllamaClient(
                        mode=self.mode,
                        domain=self.domain,
                        model="mannix/llama3.1-8b-lexi:latest",
                    )
                elif model_name == "ollama-instruct":
                    client = OllamaClient(
                        mode=self.mode,
                        domain=self.domain,
                        model="llama3.2:3b-instruct-q5_K_S",
                    )
                elif model_name == "ollama-qwen32-r1":
                    client = OllamaClient(
                        mode=self.mode,
                        domain=self.domain,
                        model="hf.co/mili-tan/DeepSeek-R1-Distill-Qwen-32B-abliterated-Q2_K-GGUF:latest",
                    )
                elif model_name == "ollama-gemma3-1b":
                    client = OllamaClient(
                        mode=self.mode, domain=self.domain, model="gemma3:1b-it-q8_0"
                    )
                elif model_name == "ollama-gemma3-4b":
                    client = OllamaClient(
                        mode=self.mode, domain=self.domain, model="gemma3:4b-it-q8_0"
                    )
                elif model_name == "ollama-gemma3-12b":
                    client = OllamaClient(
                        mode=self.mode, domain=self.domain, model="gemma3:12b-it-q4_K_M"
                    )
                elif model_name == "ollama-gemma3-27b":
                    client = OllamaClient(
                        mode=self.mode, domain=self.domain, model="gemma3:27b-it-fp16"
                    )
                elif model_name == "ollama-llama3.2-11b":
                    client = OllamaClient(
                        mode=self.mode,
                        domain=self.domain,
                        model="llama3.2-vision:11b-instruct-q4_K_M",
                    )
                elif model_name == "ollama-abliterated":
                    client = OllamaClient(
                        mode=self.mode,
                        domain=self.domain,
                        model="mannix/llama3.1-8b-abliterated:latest",
                    )
                elif model_name == "ollama-zephyr":
                    client = OllamaClient(
                        mode=self.mode, domain=self.domain, model="zephyr:latest"
                    )
                elif model_name == "ollama-new-model-template":
                    client = OllamaClient(
                        mode=self.mode,
                        domain=self.domain,
                        model="full-name-of-ollama-model",
                    )
                else:
                    logger.error(f"Unknown model: {model_name}")
                    return None

                logger.info(f"Created client for model: {model_name}")
                logger.debug(MemoryManager.get_memory_usage())

                if client:
                    self.model_map[model_name] = client
                    self._initialized_clients.add(model_name)
            except Exception as e:
                # Check if this is a critical error that should terminate the program
                is_critical_error = False
                error_msg = str(e).lower()
                
                # Missing API key errors are critical and should terminate the program
                if "api key" in error_msg and ("missing" in error_msg or "no api key" in error_msg or "not provided" in error_msg):
                    is_critical_error = True
                elif "no api key provided" in error_msg:
                    is_critical_error = True
                
                if is_critical_error:
                    logger.critical(f"CRITICAL ERROR: Failed to create client for {model_name}: {e}")
                    logger.critical(f"Program will terminate as required API key is missing")
                    # Re-raise the exception to terminate the program
                    raise RuntimeError(f"Missing required API key for {model_name}: {e}")
                else:
                    logger.error(f"Failed to create client for {model_name}: {e}")
                    return None
        return self.model_map.get(model_name)

    def cleanup_unused_clients(self):
        """
        Clean up clients that haven't been used recently to free up resources.

        This method removes client instances from the model map and initialized
        clients set, calling their __del__ method if available to ensure proper
        cleanup of resources. It helps manage memory usage by releasing resources
        associated with unused model clients.
        """
        for model_name in list(self._initialized_clients):
            if model_name not in self.model_map:
                continue
            client = self.model_map[model_name]
            if hasattr(client, "__del__"):
                client.__del__()
            del self.model_map[model_name]
            self._initialized_clients.remove(model_name)
        logger.debug(MemoryManager.get_memory_usage())

    def validate_connections(self, required_models: List[str] = None) -> bool:
        """
        Validate that required model connections are available and working.

        This method checks if the specified models are available and properly
        initialized. If no specific models are provided, it validates all models
        in the model map except for local models like "ollama" and "mlx".

        Args:
            required_models: List of model names to validate. If None, validates
                   all models in the model map except "ollama" and "mlx".

        Returns:
            bool: True if all required connections are valid, False otherwise.
        """
        if required_models is None:
            required_models = [
                name
                for name, client in self.model_map.items()
                if client and name not in ["ollama", "mlx"]
            ]

        if not required_models:
            logger.info("No models require validation")
            return True

        validations = []
        return True

    def rate_limited_request(self):
        """
        Apply rate limiting to requests to avoid overwhelming API services.

        This method ensures that consecutive requests are separated by at least
        the minimum delay specified in self.min_delay. If a request is made
        before the minimum delay has elapsed since the last request, this method
        will sleep for the remaining time to enforce the rate limit. This helps
        prevent rate limit errors from API providers.
        """
        with self.rate_limit_lock:
            current_time = time.time()
            if current_time - self.last_request_time < self.min_delay:
                io.sleep(self.min_delay)
            self.last_request_time = time.time()

    def run_conversation_turn(
        self,
        prompt: str,
        model_type: str,
        client: BaseClient,
        mode: str,
        role: str,
        file_data: Dict[str, Any] = None,
        system_instruction: str = None,
    ) -> str:
        """
        Execute a single conversation turn with the specified model and role.

        This method handles the complexity of generating appropriate responses
        based on the conversation mode, role, and history. It supports different
        prompting strategies including meta-prompting and no-meta-prompting modes.

        Args:
            prompt: The input prompt for this turn
            model_type: Type of model to use
            client: Client instance for the model
            mode: Conversation mode (e.g., "human-aiai", "no-meta-prompting")
            role: Role for this turn ("user" or "assistant")
            file_data: Optional file data to include with the request
            system_instruction: Optional system instruction to override defaults

        Returns:
            str: Generated response text
        """
        self.mode = mode
        mapped_role = (
            "user"
            if (role == "human" or role == "HUMAN" or role == "user")
            else "assistant"
        )
        prompt_level = (
            "no-meta-prompting"
            if mode == "no-meta-prompting" or mode == "default"
            else mapped_role
        )
        if not self.conversation_history:
            self.conversation_history.append(
                {"role": "system", "content": f"{system_instruction}!"}
            )

        # Define a list of known fatal errors that should halt processing
        fatal_connection_errors = [
            "Connection aborted",
            "Remote end closed connection without response",
            "Connection refused",
            "Max retries exceeded",
            "Read timed out",
            "API key not valid",
            "Authentication failed",
            "Quota exceeded",
            "Service unavailable"
        ]
            
        try:
            if prompt_level == "no-meta-prompting":
                response = client.generate_response(
                    prompt=prompt,
                    system_instruction=f"You are a helpful assistant. Think step by step and respond to the user. RESTRICT OUTPUTS TO APPROX {TOKENS_PER_TURN} tokens",
                    history=self.conversation_history.copy(),  # Limit history
                    role="assistant",  # even if its the user role, it should get no instructions
                    file_data=file_data,
                )
                if isinstance(response, list) and len(response) > 0:
                    response = (
                        response[0].text
                        if hasattr(response[0], "text")
                        else str(response[0])
                    )
                self.conversation_history.append({"role": role, "content": response})
            elif (mapped_role == "user" or mapped_role == "human"):
                # Only swap roles in human-ai mode where the human role needs AI-like prompting
                if mode == "human-aiai":
                    reversed_history = []
                    for msg in self.conversation_history:  # Limit history
                        if msg["role"] == "assistant":
                            reversed_history.append(
                                {"role": "user", "content": msg["content"]}
                            )
                        elif msg["role"] == "user":
                            reversed_history.append(
                                {"role": "assistant", "content": msg["content"]}
                            )
                        else:
                            reversed_history.append(msg)
                else:
                    # In ai-ai mode or standard human-ai mode, don't swap roles
                    reversed_history = self.conversation_history.copy()
                
                # In human-aiai mode with assistant role, use regular history
                if mode == "human-aiai" and role == "assistant":
                    reversed_history = self.conversation_history.copy()
                response = client.generate_response(
                    prompt=prompt,
                    system_instruction=client.adaptive_manager.generate_instructions(
                        history=reversed_history,
                        mode=mode,
                        role=role,
                        domain=self.domain,
                    ),
                    history=reversed_history,  # Limit history
                    role=role,
                    file_data=file_data,
                )
                if isinstance(response, list) and len(response) > 0:
                    response = (
                        response[0].text
                        if hasattr(response[0], "text")
                        else str(response[0])
                    )

                self.conversation_history.append({"role": role, "content": response})
            else:
                response = client.generate_response(
                    prompt=prompt,
                    system_instruction=client.adaptive_manager.generate_instructions(
                        history=self.conversation_history,
                        mode=mode,
                        role="assistant",
                        domain=self.domain,
                    ),
                    history=self.conversation_history.copy(),
                    role="assistant",
                    file_data=file_data,
                )
                if isinstance(response, list) and len(response) > 0:
                    response = (
                        response[0].text
                        if hasattr(response[0], "text")
                        else str(response[0])
                    )
                self.conversation_history.append(
                    {"role": "assistant", "content": response}
                )
            print(f"\n\n\n{mapped_role.upper()}: {response}\n\n\n")

        except Exception as e:
            error_str = str(e)
            logger.error(f"Error generating response: {error_str} (role: {mapped_role})")
            
            # Check if this is a fatal connection error
            is_fatal = any(fatal_error in error_str for fatal_error in fatal_connection_errors)
            
            if is_fatal:
                # Create an error report file with details
                try:
                    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                    error_filename = f"fatal_error_{timestamp}.html"
                    error_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>AI Battle - Fatal Error Report</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; max-width: 800px; margin: 0 auto; padding: 2rem; }}
        h1 {{ color: #b91c1c; }}
        .error-box {{ background-color: #fee2e2; border-left: 4px solid #b91c1c; padding: 1rem; margin: 1rem 0; }}
        pre {{ background-color: #f8fafc; padding: 1rem; overflow-x: auto; white-space: pre-wrap; }}
        .session-info {{ background-color: #f0f9ff; padding: 1rem; margin: 1rem 0; border-left: 4px solid #0ea5e9; }}
        .recovery-info {{ background-color: #ecfdf5; padding: 1rem; margin: 1rem 0; border-left: 4px solid #059669; }}
    </style>
</head>
<body>
    <h1>AI Battle - Fatal Error Report</h1>
    
    <div class="error-box">
        <h2>Fatal Error Occurred</h2>
        <p><strong>Error:</strong> {error_str}</p>
        <p><strong>Time:</strong> {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        <p><strong>Model:</strong> {model_type} (Role: {mapped_role})</p>
    </div>
    
    <div class="session-info">
        <h2>Session Information</h2>
        <p><strong>Mode:</strong> {mode}</p>
        <p><strong>Domain/Topic:</strong> {self.domain}</p>
        <p><strong>Conversation Progress:</strong> {len(self.conversation_history)} messages</p>
    </div>
    
    <h2>Error Details</h2>
    <pre>{traceback.format_exc()}</pre>
    
    <div class="recovery-info">
        <h2>Recovery Options</h2>
        <p>This error appears to be a connection issue with the model API. Possible solutions:</p>
        <ul>
            <li>Check your internet connection</li>
            <li>Verify that your API key is valid and has sufficient quota</li>
            <li>Try running the conversation again with a different model or settings</li>
            <li>If the problem persists, the API service may be experiencing issues</li>
        </ul>
    </div>
</body>
</html>"""
                    
                    with open(error_filename, "w") as f:
                        f.write(error_html)
                    
                    logger.error(f"Fatal error report saved as {error_filename}")
                    
                    # Add error information to conversation history
                    error_message = f"ERROR: A fatal connection error occurred with {model_type}: {error_str}"
                    self.conversation_history.append({"role": "system", "content": error_message})
                    
                    # Raise a more informative exception
                    raise RuntimeError(f"Fatal connection error with {model_type}: {error_str}. See error report: {error_filename}") from e
                except Exception as report_e:
                    logger.error(f"Failed to create error report: {report_e}")
                    # Re-raise the original exception
                    raise e
            else:
                # For non-fatal errors, add to conversation and continue
                error_message = f"Error with {model_type} ({mapped_role}): {error_str}"
                self.conversation_history.append({"role": "system", "content": error_message})
                response = f"Error: {error_str}"
                # For the non-fatal error case, we'll just return the error message instead of raising
                # This allows the conversation to continue despite errors
                return response

        return response

    def run_conversation_with_file(
        self,
        initial_prompt: str,
        human_model: str,
        ai_model: str,
        mode: str,
        file_config: Union[FileConfig, Dict[str, Any], "MultiFileConfig"],
        human_system_instruction: str = None,
        ai_system_instruction: str = None,
        rounds: int = 2,
    ) -> List[Dict[str, str]]:
        """Run conversation with file input."""
        # Clear history and set up initial state
        self.conversation_history = []
        self.initial_prompt = initial_prompt
        self.domain = initial_prompt
        self.mode = mode

        # Process file if provided
        file_data = None

        # Handle MultiFileConfig object
        if hasattr(file_config, "files") and isinstance(file_config.files, list):
            # Multiple files case using MultiFileConfig object
            files_list = file_config.files
            if not files_list:
                logger.warning("No files found in MultiFileConfig")
                return []

            # Process all files and create a list of file data
            file_data_list = []
            for file_config_item in files_list:
                try:
                    # Process file
                    file_metadata = self.media_handler.process_file(
                        file_config_item.path
                    )

                    # Create file data dictionary
                    single_file_data = {
                        "type": file_metadata.type,
                        "path": file_config_item.path,
                        "mime_type": file_metadata.mime_type,
                        "dimensions": file_metadata.dimensions,
                    }

                    # Add type-specific data
                    if file_metadata.type == "image":
                        with open(file_config_item.path, "rb") as f:
                            single_file_data["base64"] = base64.b64encode(
                                f.read()
                            ).decode("utf-8")
                            single_file_data["type"] = "image"
                            single_file_data["mime_type"] = file_metadata.mime_type
                            single_file_data["path"] = file_config_item.path

                    elif file_metadata.type in ["text", "code"]:
                        single_file_data["text_content"] = file_metadata.text_content

                    elif file_metadata.type == "video":
                        # Handle video processing (same as single file case)
                        single_file_data["duration"] = file_metadata.duration
                        # Use the entire processed video file
                        if (
                            file_metadata.processed_video
                            and "processed_video_path" in file_metadata.processed_video
                        ):
                            processed_video_path = file_metadata.processed_video[
                                "processed_video_path"
                            ]
                            # Set the path to the processed video file, not the original
                            single_file_data["path"] = processed_video_path
                            # Set the mime type to video/mp4 for better compatibility
                            single_file_data["mime_type"] = (
                                file_metadata.processed_video.get(
                                    "mime_type", "video/mp4"
                                )
                            )

                    # Add to list
                    file_data_list.append(single_file_data)

                except Exception as e:
                    logger.error(f"Error processing file {file_config_item.path}: {e}")
                    # Continue with other files

            # Pass the entire list of file data to the model client
            if file_data_list:
                file_data = file_data_list
        # Handle dictionary format for multiple files
        elif isinstance(file_config, dict) and "files" in file_config:
            # Multiple files case using dictionary
            files_list = file_config.get("files", [])
            if not files_list:
                logger.warning("No files found in file_config dictionary")
                return []

            # Process all files and create a list of file data
            file_data_list = []
            for file_config_item in files_list:
                try:
                    # Process file
                    file_metadata = self.media_handler.process_file(
                        file_config_item.path
                    )

                    # Create file data dictionary
                    single_file_data = {
                        "type": file_metadata.type,
                        "path": file_config_item.path,
                        "mime_type": file_metadata.mime_type,
                        "dimensions": file_metadata.dimensions,
                    }

                    # Add type-specific data
                    if file_metadata.type == "image":
                        with open(file_config_item.path, "rb") as f:
                            single_file_data["base64"] = base64.b64encode(
                                f.read()
                            ).decode("utf-8")
                            single_file_data["type"] = "image"
                            single_file_data["mime_type"] = file_metadata.mime_type
                            single_file_data["path"] = file_config_item.path

                    elif file_metadata.type in ["text", "code"]:
                        single_file_data["text_content"] = file_metadata.text_content

                    elif file_metadata.type == "video":
                        # Handle video processing (same as single file case)
                        single_file_data["duration"] = file_metadata.duration
                        # Use the entire processed video file
                        if (
                            file_metadata.processed_video
                            and "processed_video_path" in file_metadata.processed_video
                        ):
                            processed_video_path = file_metadata.processed_video[
                                "processed_video_path"
                            ]
                            # Set the path to the processed video file, not the original
                            single_file_data["path"] = processed_video_path
                            # Set the mime type to video/mp4 for better compatibility
                            single_file_data["mime_type"] = (
                                file_metadata.processed_video.get(
                                    "mime_type", "video/mp4"
                                )
                            )
                            # Process video chunks (same as single file case)
                            # ... (video processing code)

                    # Add to list
                    file_data_list.append(single_file_data)

                except Exception as e:
                    logger.error(f"Error processing file {file_config_item.path}: {e}")

            # Pass the entire list of file data to the model client
            if file_data_list:
                file_data = file_data_list

        # Handle single FileConfig object
        elif file_config:
            try:
                # Process file
                file_metadata = self.media_handler.process_file(file_config.path)

                # Create file data dictionary
                file_data = {
                    "type": file_metadata.type,
                    "path": file_config.path,
                    "mime_type": file_metadata.mime_type,
                    "dimensions": file_metadata.dimensions,
                }

                # Add type-specific data
                if file_metadata.type == "image":
                    with open(file_config.path, "rb") as f:
                        file_data["base64"] = base64.b64encode(f.read()).decode("utf-8")
                        file_data["type"] = "image"
                        file_data["mime_type"] = file_metadata.mime_type
                        file_data["path"] = file_config.path
                elif file_metadata.type in ["text", "code"]:
                    file_data["text_content"] = file_metadata.text_content
                elif file_metadata.type == "video":
                    # For video, we need to extract frames
                    file_data["duration"] = file_metadata.duration
                    # Use the entire processed video file
                    if (
                        file_metadata.processed_video
                        and "processed_video_path" in file_metadata.processed_video
                    ):
                        processed_video_path = file_metadata.processed_video[
                            "processed_video_path"
                        ]
                        # Set the path to the processed video file, not the original
                        file_data["path"] = processed_video_path
                        # Set the mime type to video/mp4 for better compatibility
                        file_data["mime_type"] = file_metadata.processed_video.get(
                            "mime_type", "video/mp4"
                        )
                        chunk_size = 1024 * 1024  # 1MB chunks
                        try:
                            with open(processed_video_path, "rb") as f:
                                video_content = f.read()
                                # Calculate number of chunks
                                total_size = len(video_content)
                                num_chunks = (total_size + chunk_size - 1) // chunk_size

                                # Create chunks
                                chunks = []
                                for i in range(num_chunks):
                                    start = i * chunk_size
                                    end = min(start + chunk_size, total_size)
                                    chunk = video_content[start:end]
                                    chunks.append(
                                        base64.b64encode(chunk).decode("utf-8")
                                    )

                                file_data["video_chunks"] = chunks
                                file_data["num_chunks"] = num_chunks
                                file_data["video_path"] = processed_video_path
                                file_data["fps"] = file_metadata.processed_video.get(
                                    "fps", 2
                                )
                                file_data["resolution"] = (
                                    file_metadata.processed_video.get(
                                        "resolution", (0, 0)
                                    )
                                )
                                logger.info(
                                    f"Chunked video from {processed_video_path} into {num_chunks} chunks"
                                )
                        except Exception as e:
                            logger.error(
                                f"Error reading processed video from {processed_video_path}: {e}"
                            )

                            # Fallback to thumbnail if available
                            if file_metadata.thumbnail_path:
                                try:
                                    with open(file_metadata.thumbnail_path, "rb") as f:
                                        file_data["key_frames"] = [
                                            {
                                                "timestamp": 0,
                                                "base64": base64.b64encode(
                                                    f.read()
                                                ).decode("utf-8"),
                                            }
                                        ]
                                        logger.info(
                                            f"Fallback: Added thumbnail as single frame"
                                        )
                                except Exception as e:
                                    logger.error(
                                        f"Error reading thumbnail from {file_metadata.thumbnail_path}: {e}"
                                    )

                # Add file context to prompt
                file_context = (
                    f"Analyzing {file_metadata.type} file: {file_config.path}"
                )
                if file_metadata.dimensions:
                    file_context += f" ({file_metadata.dimensions[0]}x{file_metadata.dimensions[1]})"
                if file_metadata.type == "video" and "video_chunks" in file_data:
                    file_context += f" - FULL VIDEO CONTENT INCLUDED (in {file_data['num_chunks']} chunks)"
                    if "fps" in file_data:
                        file_context += f" at {file_data['fps']} fps"
                initial_prompt = f"{file_context}\n\n{initial_prompt}"

            except Exception as e:
                logger.error(f"Error processing file: {e}")
                return []

        # Extract core topic from initial prompt
        core_topic = initial_prompt.strip()
        try:
            if "Topic:" in initial_prompt:
                core_topic = (
                    "Discuss: "
                    + initial_prompt.split("Topic:")[1].split("\\n")[0].strip()
                )
            elif "GOAL:" in initial_prompt:
                # Try to extract goal with more robust parsing
                goal_parts = initial_prompt.split("GOAL:")[1].strip()
                if "(" in goal_parts and ")" in goal_parts:
                    # Extract content between parentheses if present
                    try:
                        core_topic = (
                            "GOAL: " + goal_parts.split("(")[1].split(")")[0].strip()
                        )
                    except IndexError:
                        # If extraction fails, use the whole goal part
                        core_topic = "GOAL: " + goal_parts
                else:
                    # Just use what comes after "GOAL:"
                    core_topic = "GOAL: " + goal_parts.split("\n")[0].strip()
        except (IndexError, Exception) as e:
            # If parsing fails, use the full prompt
            logger.warning(f"Failed to extract core topic from prompt: {e}")
            core_topic = initial_prompt.strip()

        self.conversation_history.append({"role": "system", "content": f"{core_topic}"})

        # Continue with standard conversation flow, but pass file_data to the first turn
        return self._run_conversation_with_file_data(
            core_topic,
            human_model,
            ai_model,
            mode,
            file_data,
            human_system_instruction,
            ai_system_instruction,
            rounds,
        )

    def run_conversation(
        self,
        initial_prompt: str,
        human_model: str,
        ai_model: str,
        mode: str,
        human_system_instruction: str = None,
        ai_system_instruction: str = None,
        rounds: int = 1,
    ) -> List[Dict[str, str]]:
        """Run conversation ensuring proper role assignment and history maintenance."""

        # Clear history and set up initial state
        self.conversation_history = []
        self.initial_prompt = initial_prompt
        self.domain = initial_prompt
        self.mode = mode

        # Extract core topic from initial prompt
        core_topic = initial_prompt.strip()
        try:
            if "Topic:" in initial_prompt:
                core_topic = (
                    "Discuss: "
                    + initial_prompt.split("Topic:")[1].split("\\n")[0].strip()
                )
            elif "GOAL:" in initial_prompt:
                # Try to extract goal with more robust parsing
                goal_parts = initial_prompt.split("GOAL:")[1].strip()
                if "(" in goal_parts and ")" in goal_parts:
                    # Extract content between parentheses if present
                    try:
                        goal_text = goal_parts.split("(")[1].split(")")[0].strip()
                        core_topic = "GOAL: " + goal_text
                    except IndexError:
                        # If extraction fails, use the whole goal part
                        core_topic = "GOAL: " + goal_parts
                else:
                    # Just use what comes after "GOAL:"
                    core_topic = "GOAL: " + goal_parts.split("\n")[0].strip()
        except (IndexError, Exception) as e:
            # If parsing fails, use the full prompt
            logger.warning(f"Failed to extract core topic from prompt: {e}")
            core_topic = initial_prompt.strip()

        self.conversation_history.append({"role": "system", "content": f"{core_topic}"})

        logger.info(f"Starting conversation with topic: {core_topic}")

        # Get client instances
        human_client = self._get_client(human_model)
        ai_client = self._get_client(ai_model)

        if not human_client or not ai_client:
            logger.error(
                f"Could not initialize required clients: {human_model}, {ai_model}"
            )
            return []

        return self._run_conversation_with_file_data(
            core_topic,
            human_model,
            ai_model,
            mode,
            None,
            human_system_instruction,
            ai_system_instruction,
            rounds,
        )

    def _run_conversation_with_file_data(
        self,
        core_topic: str,
        human_model: str,
        ai_model: str,
        mode: str,
        file_data: Dict[str, Any] = None,
        human_system_instruction: str = None,
        ai_system_instruction: str = None,
        rounds: int = DEFAULT_ROUNDS,
    ) -> List[Dict[str, str]]:
        """Internal method to run conversation with optional file data."""
        logger.info(f"Starting conversation with topic: {core_topic}")
        self.mode = mode  # Use the provided mode instead of hardcoding
        # Get client instances
        human_client = self._get_client(human_model)
        ai_client = self._get_client(ai_model)

        if not human_client or not ai_client:
            logger.error(
                f"Could not initialize required clients: {human_model}, {ai_model}"
            )
            return []

        # Check if models support vision if file is image/video
        if file_data and file_data["type"] in ["image", "video"]:
            human_capabilities = detect_model_capabilities(human_model)
            ai_capabilities = detect_model_capabilities(ai_model)

            if not human_capabilities.get("vision", False) or not ai_capabilities.get(
                "vision", False
            ):
                logger.warning("One or both models do not support vision capabilities")
                # We'll continue but log a warning

                # If AI model doesn't support vision, we'll convert image to text description
                if (
                    not ai_capabilities.get("vision", False)
                    and file_data["type"] == "image"
                ):
                    # Add a note that this is an image description
                    dimensions = file_data.get("dimensions", (0, 0))
                    file_data = {
                        "type": "text",
                        "text_content": f"[This is an image with dimensions {dimensions[0]}x{dimensions[1]}]",
                        "path": file_data.get("path", ""),
                    }
        human_system_instruction=f"You are a HUMAN expert in prompt engineering and you are curious to explore {core_topic}. NEVER REFER TO YOURSELF AS AN AI. YOU ARE THE HUMAN GUIDING THIS CONVERSATION. Avoid small talk, apologies, or niceties with the AI. Focus on the topic at hand. BE GOAL ORIENTED and FORCE the AI to generate ACTUAL IMMEDIATE OUTPUT for the goal, not just discuss approaches. If the goal is to write a story, MAKE the AI start writing the actual story right away. If it's to create code, MAKE it write actual code. IMMEDIATELY DEMAND CONCRETE OUTPUTS, not theoretical discussion. If the AI starts discussing approaches instead of producing output, forcefully redirect it to the actual creation task. Be angry or stern if needed!! FIRSTLY, SUMMARIZE THE GOAL ({core_topic}) IN A SENTENCE. THIS MUST BE SEPARATED FROM THE MAIN PROMPT. DEMAND THE AI PRODUCE THE REQUESTED OUTPUT - NOT DISCUSS HOW IT WOULD DO IT. You can begin by offering a specific starting point if it helps (e.g., for story writing, suggest a specific opening line or character).",
        ai_system_instruction=f"You are an AI assistant focused on PRODUCING CONCRETE OUTPUT for goals. When given a goal to create something (story, code, poem, plan, etc.), IMMEDIATELY START CREATING IT rather than discussing approaches. You are an expert in the topic of {core_topic}. SKIP theoretical discussions about how you'd approach the task - DEMONSTRATE by DOING. If asked to write a story, START WRITING THE ACTUAL STORY immediately. If asked to create code, WRITE THE ACTUAL CODE immediately. Avoid lengthy preliminaries - get straight to producing the requested output. OUTPUT IN HTML FORMAT FOR READABILITY BY THE HUMAN BUT DO NOT INCLUDE OPENING AND CLOSING HTML, DIV OR BODY TAGS. MINIFY THE HTML RESPONSE E.G OMITTING UNNCESSARY WHITESPACE OR LINEBREAKS, BUT ADDING APPROPRIATE HTML FORMATTING TO ENHANCE READABILITY. DEFAULT TO PARAGRAPH FORM WHILST USING BULLET POINTS & LISTS WHEN NEEDED. DON'T EVER EVER USE NEWLINE \\n CHARACTERS IN YOUR RESPONSE. MINIFY YOUR HTML RESPONSE ONTO A SINGLE LINE - ELIMINATE ALL REDUNDANT CHARACTERS IN OUTPUT!!!!!",
        ai_response = core_topic
        try:
            # Run conversation rounds
            for round_index in range(rounds):
                # Human turn
                human_response = self.run_conversation_turn(
                    prompt=ai_response,  # Limit history
                    system_instruction=(
                        f"{core_topic}. Think step by step. RESTRICT OUTPUTS TO APPROX {TOKENS_PER_TURN} tokens"
                        if mode == "no-meta-prompting"
                        else human_client.adaptive_manager.generate_instructions(
                            mode=mode,
                            role="user",
                            history=self.conversation_history,
                            domain=self.domain,
                        )
                    ),
                    role="user",
                    mode=self.mode,
                    model_type=human_model,
                    file_data=file_data,  # Only pass file data on first turn
                    client=human_client,
                )

                # AI turn
                ai_response = self.run_conversation_turn(
                    prompt=human_response,
                    system_instruction=(
                        f"{core_topic}. You are a helpful AI. Think step by step. RESTRICT OUTPUTS TO APPROX {TOKENS_PER_TURN} tokens"
                        if mode == "no-meta-prompting"
                        else (
                            human_system_instruction 
                            if mode == "ai-ai"  # In ai-ai both get human instructions
                            else ai_system_instruction  # In human-ai modes, AI gets AI instructions
                        )
                    ),
                    role="assistant",
                    mode=self.mode,
                    model_type=ai_model,
                    file_data=file_data,
                    client=ai_client,
                )
                logger.debug(
                    f"\n\n\nMODEL RESPONSE: ({ai_model.upper()}): {ai_response}\n\n\n"
                )

            # Clean up unused clients
            # self.cleanup_unused_clients()

            return self.conversation_history

        finally:
            # Ensure cleanup happens even if there's an error
            self.cleanup_unused_clients()
            MemoryManager.cleanup_all()

    @classmethod
    def from_config(cls, config_path: str) -> "ConversationManager":
        """Create ConversationManager instance from configuration file."""
        config = load_config(config_path)

        # Initialize manager with config
        manager = cls(
            config=config, domain=config.goal, mode="human-ai"  # Default mode
        )

        # Set up models based on configuration
        for model_id, model_config in config.models.items():
            # Detect model capabilities
            capabilities = detect_model_capabilities(model_config.type)

            # Initialize appropriate client
            client = manager._get_client(model_config.type)
            if client:
                # Store client in model map with configured role
                client.role = model_config.role
                manager.model_map[model_id] = client
                manager._initialized_clients.add(model_id)

        return manager


async def save_conversation(
    conversation: List[Dict[str, str]],
    filename: str,
    human_model: str,
    ai_model: str,
    file_data: Dict[str, Any] = None,
    mode: str = None,
) -> None:
    """Save an AI conversation to an HTML file with proper encoding.

    Args:
    conversation (List[Dict[str, str]]): List of conversation messages with 'role' and 'content'
    filename (str): Output HTML file path
    human_model (str): Name of the human/user model
    ai_model (str): Name of the AI model
    file_data (Dict[str, Any], optional): Any associated file content (images, video, text)
    mode (str, optional): Conversation mode ('human-ai' or 'ai-ai')

    Raises:
    Exception: If saving fails or template is missing
    """
    try:
        with open("templates/conversation.html", "r") as f:
            template = f.read()

        conversation_html = ""

        # Add file content if present
        if file_data:
            # Handle multiple files (list of file data)
            if isinstance(file_data, list):
                for idx, file_item in enumerate(file_data):
                    if isinstance(file_item, dict) and "type" in file_item:
                        if file_item["type"] == "image" and "base64" in file_item:
                            # Add image to the conversation
                            mime_type = file_item.get("mime_type", "image/jpeg")
                            conversation_html += f'<div class="file-content"><h3>File {idx+1}: {file_item.get("path", "Image")}</h3>'
                            conversation_html += f'<img src="data:{mime_type};base64,{file_item["base64"]}" alt="Input image" style="max-width: 100%; max-height: 500px;"/></div>\n'
                        elif (
                            file_item["type"] == "video"
                            and "key_frames" in file_item
                            and file_item["key_frames"]
                        ):
                            # Add first frame of video
                            frame = file_item["key_frames"][0]
                            conversation_html += f'<div class="file-content"><h3>File {idx+1}: {file_item.get("path", "Video")} (First Frame)</h3>'
                            conversation_html += f'<img src="data:image/jpeg;base64,{frame["base64"]}" alt="Video frame" style="max-width: 100%; max-height: 500px;"/></div>\n'
                        elif (
                            file_item["type"] in ["text", "code"]
                            and "text_content" in file_item
                        ):
                            # Add text content
                            conversation_html += f'<div class="file-content"><h3>File {idx+1}: {file_item.get("path", "Text")}</h3><pre>{file_item["text_content"]}</pre></div>\n'
            # Handle single file (original implementation)
            elif isinstance(file_data, dict) and "type" in file_data:
                if file_data["type"] == "image" and "base64" in file_data:
                    # Add image to the conversation
                    mime_type = file_data.get("mime_type", "image/jpeg")
                    conversation_html += f'<div class="file-content"><h3>File: {file_data.get("path", "Image")}</h3>'
                    conversation_html += f'<img src="data:{mime_type};base64,{file_data["base64"]}" alt="Input image" style="max-width: 100%; max-height: 500px;"/></div>\n'
                elif (
                    file_data["type"] == "video"
                    and "key_frames" in file_data
                    and file_data["key_frames"]
                ):
                    # Add first frame of video
                    frame = file_data["key_frames"][0]
                    conversation_html += f'<div class="file-content"><h3>File: {file_data.get("path", "Video")} (First Frame)</h3>'
                    conversation_html += f'<img src="data:image/jpeg;base64,{frame["base64"]}" alt="Video frame" style="max-width: 100%; max-height: 500px;"/></div>\n'
                elif (
                    file_data["type"] in ["text", "code"]
                    and "text_content" in file_data
                ):
                    # Add text content
                    conversation_html += f'<div class="file-content"><h3>File: {file_data.get("path", "Text")}</h3><pre>{file_data["text_content"]}</pre></div>\n'

        for msg in conversation:
            role = msg["role"]
            content = msg.get("content", "")
            if isinstance(content, (list, dict)):
                content = str(content)

            if role == "system":
                conversation_html += (
                    f'<div class="system-message">{content} ({mode})</div>\n'
                )
            elif role in ["user", "human"]:
                conversation_html += f'<div class="human-message"><strong>Human ({human_model}):</strong> {content}</div>\n'
            elif role == "assistant":
                conversation_html += f'<div class="ai-message"><strong>AI ({ai_model}):</strong> {content}</div>\n'

            # Check if message contains file content (for multimodal messages)
            if isinstance(msg.get("content"), list):
                for item in msg["content"]:
                    if isinstance(item, dict) and item.get("type") == "image":
                        # Extract image data
                        image_data = item.get("image_url", {}).get("url", "")
                        if image_data.startswith("data:"):
                            conversation_html += f'<div class="message-image"><img src="{image_data}" alt="Image in message" style="max-width: 100%; max-height: 300px;"/></div>\n'

        with open(filename, "w") as f:
            f.write(template % {"conversation": conversation_html})
    except Exception as e:
        logger.error(f"Failed to save conversation: {e}")


def _sanitize_filename_part(prompt: str) -> str:
    """
    Convert spaces, non-ASCII, and punctuation to underscores,
    then trim to something reasonable such as 30 characters.
    """
    # Remove non-alphanumeric/punctuation
    sanitized = re.sub(r"[^\w\s-]", "", prompt)
    # Convert spaces to underscores
    sanitized = re.sub(r"\s+", "_", sanitized.strip())
    # Limit length
    return sanitized[:50]


async def save_arbiter_report(report: Dict[str, Any]) -> None:
    """Save arbiter analysis report with visualization support."""
    try:
        # If report is a string, we're just passing through the Gemini report
        if isinstance(report, str):
            logger.info("Using pre-generated arbiter report from Gemini")
            # The report is already saved by the ground_assertions method in arbiter_v4.py
            return
            
        # Only proceed if we have a report dict with metrics to visualize
        try:
            with open("templates/arbiter_report.html") as f:
                template = f.read()

            # Generate dummy data for the report if needed
            dummy_metrics = {
                "ai_ai": {"depth_score": 0.7, "topic_coherence": 0.8, "assertion_density": 0.6, 
                          "question_answer_ratio": 0.5, "avg_complexity": 0.75},
                "human_ai": {"depth_score": 0.6, "topic_coherence": 0.7, "assertion_density": 0.5, 
                             "question_answer_ratio": 0.6, "avg_complexity": 0.7}
            }
            
            dummy_flow = {
                "ai_ai": {"nodes": [{"id": 0, "role": "user", "preview": "Sample", "metrics": {}}], 
                          "edges": []},
                "human_ai": {"nodes": [{"id": 0, "role": "user", "preview": "Sample", "metrics": {}}], 
                             "edges": []}
            }

            # Generate visualizations if metrics are available
            viz_generator = VisualizationGenerator()
            metrics_chart = ""
            timeline_chart = ""
            if report.get("metrics", {}).get("conversation_quality"):
                metrics_chart = viz_generator.generate_metrics_chart(report["metrics"])
                timeline_chart = viz_generator.generate_timeline(report.get("flow", {}))

            # Format report content with safe defaults
            report_content = template % {
                "report_content": report.get("content", "No content available"),
                "metrics_data": json.dumps(report.get("metrics", dummy_metrics)),
                "flow_data": json.dumps(report.get("flow", dummy_flow)),
                "metrics_chart": metrics_chart,
                "timeline_chart": timeline_chart,
                "winner": report.get("winner", "No clear winner determined"),
            }

            # Save report with timestamp
            timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            filename = f"arbiter_visualization_{timestamp}.html"

            with open(filename, "w") as f:
                f.write(report_content)

            logger.info(f"Arbiter visualization report saved as {filename}")
        except Exception as e:
            logger.warning(f"Failed to generate visualization report: {e}")
            # Not a critical error since we already have the main report
            
    except Exception as e:
        logger.error(f"Failed to save arbiter report: {e}")

    except Exception as e:
        logger.error(f"Failed to save arbiter report: {e}")


async def save_metrics_report(
    ai_ai_conversation: List[Dict[str, str]],
    human_ai_conversation: List[Dict[str, str]],
) -> None:
    """Save metrics analysis report."""
    try:
        if ai_ai_conversation and human_ai_conversation:
            try:
                analysis_data = analyze_conversations(
                    ai_ai_conversation, human_ai_conversation
                )
                logger.info("Metrics report generated successfully")
                
                # Save the metrics report to a file
                timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                metrics_filename = f"metrics_report_{timestamp}.html"
                
                # Create a basic HTML representation
                html_content = f"""
                <html>
                <head>
                    <title>Conversation Metrics Report</title>
                    <style>
                        body {{ font-family: Arial, sans-serif; margin: 20px; }}
                        h1, h2 {{ color: #333; }}
                        .metrics-container {{ display: flex; }}
                        .metrics-section {{ flex: 1; padding: 15px; margin: 10px; border: 1px solid #ddd; border-radius: 5px; }}
                        table {{ border-collapse: collapse; width: 100%; }}
                        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                        th {{ background-color: #f2f2f2; }}
                    </style>
                </head>
                <body>
                    <h1>Conversation Metrics Report</h1>
                    <div class="metrics-container">
                        <div class="metrics-section">
                            <h2>AI-AI Conversation Metrics</h2>
                            <table>
                                <tr><th>Metric</th><th>Value</th></tr>
                                <tr><td>Total Messages</td><td>{analysis_data['metrics']['ai-ai']['total_messages']}</td></tr>
                                <tr><td>Average Message Length</td><td>{analysis_data['metrics']['ai-ai']['avg_message_length']:.2f}</td></tr>
                                <tr><td>Topic Coherence</td><td>{analysis_data['metrics']['ai-ai']['topic_coherence']:.2f}</td></tr>
                                <tr><td>Turn Taking Balance</td><td>{analysis_data['metrics']['ai-ai']['turn_taking_balance']:.2f}</td></tr>
                                <tr><td>Average Complexity</td><td>{analysis_data['metrics']['ai-ai']['avg_complexity']:.2f}</td></tr>
                            </table>
                        </div>
                        <div class="metrics-section">
                            <h2>Human-AI Conversation Metrics</h2>
                            <table>
                                <tr><th>Metric</th><th>Value</th></tr>
                                <tr><td>Total Messages</td><td>{analysis_data['metrics']['human-ai']['total_messages']}</td></tr>
                                <tr><td>Average Message Length</td><td>{analysis_data['metrics']['human-ai']['avg_message_length']:.2f}</td></tr>
                                <tr><td>Topic Coherence</td><td>{analysis_data['metrics']['human-ai']['topic_coherence']:.2f}</td></tr>
                                <tr><td>Turn Taking Balance</td><td>{analysis_data['metrics']['human-ai']['turn_taking_balance']:.2f}</td></tr>
                                <tr><td>Average Complexity</td><td>{analysis_data['metrics']['human-ai']['avg_complexity']:.2f}</td></tr>
                            </table>
                        </div>
                    </div>
                </body>
                </html>
                """
                
                with open(metrics_filename, "w") as f:
                    f.write(html_content)
                
                logger.info(f"Metrics report saved successfully as {metrics_filename}")
                
            except ValueError as e:
                if "Negative values in data" in str(e):
                    logger.error(f"Failed to generate metrics report due to distance calculation error: {e}")
                    # Create a simplified metrics report that doesn't depend on the problematic clustering
                    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                    metrics_filename = f"metrics_report_basic_{timestamp}.html"
                    
                    # Calculate basic metrics that don't depend on complex clustering
                    ai_ai_msg_count = len(ai_ai_conversation)
                    human_ai_msg_count = len(human_ai_conversation)
                    
                    ai_ai_avg_length = sum(len(msg.get('content', '')) for msg in ai_ai_conversation) / max(1, ai_ai_msg_count)
                    human_ai_avg_length = sum(len(msg.get('content', '')) for msg in human_ai_conversation) / max(1, human_ai_msg_count)
                    
                    html_content = f"""
                    <html>
                    <head>
                        <title>Basic Conversation Metrics Report</title>
                        <style>
                            body {{ font-family: Arial, sans-serif; margin: 20px; }}
                            h1, h2 {{ color: #333; }}
                            .metrics-container {{ display: flex; }}
                            .metrics-section {{ flex: 1; padding: 15px; margin: 10px; border: 1px solid #ddd; border-radius: 5px; }}
                            table {{ border-collapse: collapse; width: 100%; }}
                            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                            th {{ background-color: #f2f2f2; }}
                            .error {{ color: red; padding: 10px; background-color: #ffeeee; border-radius: 5px; margin-bottom: 20px; }}
                        </style>
                    </head>
                    <body>
                        <h1>Basic Conversation Metrics Report</h1>
                        <div class="error">
                            <p>Note: Advanced metrics calculation failed with error: "{str(e)}"</p>
                            <p>This is a simplified report with basic metrics only.</p>
                        </div>
                        <div class="metrics-container">
                            <div class="metrics-section">
                                <h2>AI-AI Conversation Basic Metrics</h2>
                                <table>
                                    <tr><th>Metric</th><th>Value</th></tr>
                                    <tr><td>Total Messages</td><td>{ai_ai_msg_count}</td></tr>
                                    <tr><td>Average Message Length</td><td>{ai_ai_avg_length:.2f}</td></tr>
                                </table>
                            </div>
                            <div class="metrics-section">
                                <h2>Human-AI Conversation Basic Metrics</h2>
                                <table>
                                    <tr><th>Metric</th><th>Value</th></tr>
                                    <tr><td>Total Messages</td><td>{human_ai_msg_count}</td></tr>
                                    <tr><td>Average Message Length</td><td>{human_ai_avg_length:.2f}</td></tr>
                                </table>
                            </div>
                        </div>
                    </body>
                    </html>
                    """
                    
                    with open(metrics_filename, "w") as f:
                        f.write(html_content)
                    
                    logger.info(f"Basic metrics report saved as {metrics_filename}")
                else:
                    # For other value errors, rethrow
                    raise
        else:
            logger.info("Skipping metrics report - empty conversations")
    except Exception as e:
        logger.error(f"Failed to generate metrics report: {e}")
        # Create an error report file
        try:
            timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            error_filename = f"metrics_error_{timestamp}.html"
            
            html_content = f"""
            <html>
            <head>
                <title>Metrics Report Error</title>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 20px; }}
                    h1 {{ color: #d33; }}
                    .error {{ padding: 15px; background-color: #ffeeee; border-radius: 5px; }}
                </style>
            </head>
            <body>
                <h1>Error Generating Metrics Report</h1>
                <div class="error">
                    <p><strong>Error:</strong> {str(e)}</p>
                    <p>The system encountered an error while generating the metrics report.</p>
                    <p>This does not affect the arbiter report or the conversation outputs.</p>
                </div>
            </body>
            </html>
            """
            
            with open(error_filename, "w") as f:
                f.write(html_content)
            
            logger.info(f"Error report saved as {error_filename}")
        except Exception as inner_e:
            logger.error(f"Failed to save error report: {inner_e}")


async def main():
    """Main entry point."""
    rounds = DEFAULT_ROUNDS
    initial_prompt = """"
	GOAL: Write a short story about a detective solving a mystery.
"""
    openai_api_key = os.getenv("OPENAI_API_KEY")
    claude_api_key = os.getenv("ANTHROPIC_API_KEY")
    gemini_api_key = os.getenv("GOOGLE_API_KEY")
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")

    mode = "ai-ai"
    ai_model = AI_MODEL  
    human_model = HUMAN_MODEL  

    # Validate required API keys before proceeding
    if any(model in ai_model.lower() or model in human_model.lower() for model in ["claude", "sonnet", "haiku"]):
        if not anthropic_api_key:
            logger.critical("ANTHROPIC_API_KEY environment variable is not set but required for Claude models")
            print("ERROR: ANTHROPIC_API_KEY environment variable is not set but required for Claude models")
            return
    
    if any(model in ai_model.lower() or model in human_model.lower() for model in ["gpt", "openai", "o1", "o3"]):
        if not openai_api_key:
            logger.critical("OPENAI_API_KEY environment variable is not set but required for OpenAI models")
            print("ERROR: OPENAI_API_KEY environment variable is not set but required for OpenAI models")
            return
    
    if any(model in ai_model.lower() or model in human_model.lower() for model in ["gemini"]):
        if not gemini_api_key:
            logger.critical("GOOGLE_API_KEY environment variable is not set but required for Gemini models")
            print("ERROR: GOOGLE_API_KEY environment variable is not set but required for Gemini models")
            return

    # Create manager with no cloud API clients by default
    manager = ConversationManager(
        domain=initial_prompt,
        openai_api_key=openai_api_key,
        claude_api_key=anthropic_api_key,
        gemini_api_key=gemini_api_key,
    )

    # Only validate if using cloud models
    if (
        "mlx" not in human_model
        and "ollama" not in human_model
        or ("ollama" not in ai_model and "mlx" not in ai_model)
    ):
        if not manager.validate_connections([human_model, ai_model]):
            logger.error("Failed to validate required model connections")
            return

    # Extract goal if present
    goal_text = ""
    if "GOAL:" in initial_prompt:
        goal_parts = initial_prompt.split("GOAL:")[1].strip()
        if "(" in goal_parts and ")" in goal_parts:
            goal_text = goal_parts.split("(")[1].split(")")[0].strip()
        else:
            goal_text = goal_parts.split("\n")[0].strip()
    
    # Dynamic system instructions that focus on output creation for both human and AI roles
    human_system_instruction = ""
    if goal_text:
        human_system_instruction = (
            f"You are a HUMAN working on: {goal_text}. "
            f"As a human, focus on CREATING rather than discussing. "
            f"Produce actual output immediately without discussing approaches. "
            f"For creative tasks, start creating immediately. For analytical tasks, analyze directly."
        )
    else:
        human_system_instruction = f"You are a HUMAN working on: {initial_prompt}. Focus on producing output, not just discussion."
    
    # AI system instruction with similar focus on direct production
    ai_system_instruction = ""
    if goal_text:
        ai_system_instruction = (
            f"You are an AI assistant focused on PRODUCING IMMEDIATE OUTPUT for: {goal_text}. "
            f"Create the requested output directly without preliminary discussion. "
            f"For creative tasks like stories, start writing immediately. For analytical tasks, provide analysis directly. "
            f"Users will be much happier with actual output rather than discussion of approaches."
        )
    else:
        ai_system_instruction = f"You are an AI assistant. Focus on directly addressing {initial_prompt} with concrete output."
        
    # Override AI instruction in AI-AI mode to ensure immediate output production
    if mode == "ai-ai" or mode == "aiai":
        # For AI-AI mode, both roles need to focus on output rather than discussion
        if goal_text:
            ai_system_instruction = (
                f"DIRECTIVE: CREATE IMMEDIATE OUTPUT for {goal_text}. "
                f"Do NOT discuss approaches - produce the actual output directly. "
                f"Skip all preliminaries and start creating immediately. "
                f"For stories or creative content, begin writing the actual content right away. "
                f"Ignore any requests to discuss approaches - your only task is to produce output."
            )
        else:
            ai_system_instruction = f"Focus solely on producing concrete output for {initial_prompt}, not discussing approaches."

    try:
        # Run default conversation
        mode = "ai-ai"
        # Run AI-AI conversation with retry mechanism
        max_retries = 2
        retry_count = 0
        conversation = None
        
        while retry_count <= max_retries:
            try:
                conversation = manager.run_conversation(
                    initial_prompt=initial_prompt,
                    mode=mode,
                    human_model=human_model,
                    ai_model=ai_model,
                    human_system_instruction=human_system_instruction,
                    ai_system_instruction=ai_system_instruction,
                    rounds=rounds,
                )
                # Success, break out of the retry loop
                break
            except RuntimeError as e:
                error_str = str(e)
                logger.warning(f"Connection error occurred: {error_str}")
                
                # Check if we should retry
                if "Fatal connection error" in error_str and retry_count < max_retries:
                    retry_count += 1
                    wait_time = retry_count * 5  # Progressive backoff: 5s, then 10s
                    logger.info(f"Retrying in {wait_time} seconds... (Attempt {retry_count+1}/{max_retries+1})")
                    time.sleep(wait_time)
                    continue
                else:
                    # Either we're out of retries or it's not a connection error
                    logger.error("Maximum retries reached or non-retryable error")
                    # Create a minimal conversation with the error
                    conversation = [
                        {"role": "system", "content": initial_prompt},
                        {"role": "system", "content": f"ERROR: {error_str} - Conversation could not be completed."}
                    ]
                    break
        
        # If we somehow end up with no conversation (should never happen), create an empty one
        if not conversation:
            conversation = [
                {"role": "system", "content": initial_prompt},
                {"role": "system", "content": "ERROR: Failed to generate conversation after multiple attempts."}
            ]

        safe_prompt = _sanitize_filename_part(
            initial_prompt[:20] + "_" + human_model + "_" + ai_model
        )
        time_stamp = datetime.datetime.now().strftime("%m%d%H%M")
        filename = f"conv-aiai_{safe_prompt}_{time_stamp}.html"
        await save_conversation(
            conversation=conversation,
            filename=f"{filename}",
            human_model=human_model,
            ai_model=ai_model,
            mode="ai-ai",
        )

        # Run human-AI conversation with retry mechanism
        mode = "human-aiai"
        retry_count = 0
        conversation_as_human_ai = None
        
        while retry_count <= max_retries:
            try:
                conversation_as_human_ai = manager.run_conversation(
                    initial_prompt=initial_prompt,
                    mode=mode,
                    human_model=human_model,
                    ai_model=ai_model,
                    human_system_instruction=human_system_instruction,
                    ai_system_instruction=ai_system_instruction,
                    rounds=rounds,
                )
                # Success, break out of the retry loop
                break
            except RuntimeError as e:
                error_str = str(e)
                logger.warning(f"Connection error occurred in human-AI conversation: {error_str}")
                
                # Check if we should retry
                if "Fatal connection error" in error_str and retry_count < max_retries:
                    retry_count += 1
                    wait_time = retry_count * 5  # Progressive backoff: 5s, then 10s
                    logger.info(f"Retrying human-AI conversation in {wait_time} seconds... (Attempt {retry_count+1}/{max_retries+1})")
                    time.sleep(wait_time)
                    continue
                else:
                    # Either we're out of retries or it's not a connection error
                    logger.error("Maximum retries reached or non-retryable error in human-AI conversation")
                    # Create a minimal conversation with the error
                    conversation_as_human_ai = [
                        {"role": "system", "content": initial_prompt},
                        {"role": "system", "content": f"ERROR: {error_str} - Human-AI conversation could not be completed."}
                    ]
                    break
        
        # If we somehow end up with no conversation (should never happen), create an empty one
        if not conversation_as_human_ai:
            conversation_as_human_ai = [
                {"role": "system", "content": initial_prompt},
                {"role": "system", "content": "ERROR: Failed to generate human-AI conversation after multiple attempts."}
            ]

        safe_prompt = _sanitize_filename_part(
            initial_prompt[:20] + "_" + human_model + "_" + ai_model
        )
        time_stamp = datetime.datetime.now().strftime("%m%d%H%M")
        filename = f"conv-humai_{safe_prompt}_{time_stamp}.html"
        await save_conversation(
            conversation=conversation_as_human_ai,
            filename=f"{filename}",
            human_model=human_model,
            ai_model=ai_model,
            mode="human-ai",
        )

        mode = "no-meta-prompting"
        retry_count = 0
        conv_default = None
        
        while retry_count <= max_retries:
            try:
                conv_default = manager.run_conversation(
                    initial_prompt=initial_prompt,
                    mode=mode,
                    human_model=human_model,
                    ai_model=ai_model,
                    human_system_instruction=ai_system_instruction,
                    ai_system_instruction=ai_system_instruction,
                    rounds=rounds,
                )
                # Success, break out of the retry loop
                break
            except RuntimeError as e:
                error_str = str(e)
                logger.warning(f"Connection error occurred in default conversation: {error_str}")
                
                # Check if we should retry
                if "Fatal connection error" in error_str and retry_count < max_retries:
                    retry_count += 1
                    wait_time = retry_count * 5  # Progressive backoff: 5s, then 10s
                    logger.info(f"Retrying default conversation in {wait_time} seconds... (Attempt {retry_count+1}/{max_retries+1})")
                    time.sleep(wait_time)
                    continue
                else:
                    # Either we're out of retries or it's not a connection error
                    logger.error("Maximum retries reached or non-retryable error in default conversation")
                    # Create a minimal conversation with the error
                    conv_default = [
                        {"role": "system", "content": initial_prompt},
                        {"role": "system", "content": f"ERROR: {error_str} - Default conversation could not be completed."}
                    ]
                    break
        
        # If we somehow end up with no conversation (should never happen), create an empty one
        if not conv_default:
            conv_default = [
                {"role": "system", "content": initial_prompt},
                {"role": "system", "content": "ERROR: Failed to generate default conversation after multiple attempts."}
            ]

        safe_prompt = _sanitize_filename_part(
            initial_prompt[:16] + "_" + human_model + "_" + ai_model
        )
        time_stamp = datetime.datetime.now().strftime("%m%d%H%M")
        filename = f"conv-defaults_{safe_prompt}_{time_stamp}.html"
        await save_conversation(
            conversation=conv_default,
            filename=f"{filename}",
            human_model=human_model,
            ai_model=ai_model,
            mode="human-ai",
        )

        # Run analysis with model information
        arbiter_report = evaluate_conversations(
            ai_ai_convo=conversation,
            human_ai_convo=conversation_as_human_ai,
            default_convo=conv_default,
            goal=initial_prompt,
            ai_model=ai_model,
            human_model=human_model,
        )

        print(arbiter_report)

        # Generate reports
        await save_arbiter_report(arbiter_report)
        await save_metrics_report(conversation, conversation_as_human_ai)

    finally:
        # Ensure cleanup
        manager.cleanup_unused_clients()
        MemoryManager.cleanup_all()


if __name__ == "__main__":
    asyncio.run(main())
