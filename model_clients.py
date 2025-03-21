"""Base and model-specific client implementations with memory optimizations."""
import os
import time
import logging
import random
from typing import List, Dict, Optional, Any, TypeVar, Union
from dataclasses import dataclass
from google import genai
from google.genai import types
from openai import OpenAI
from anthropic import Anthropic
from ollama import AsyncClient, ChatResponse, chat

from adaptive_instructions import AdaptiveInstructionManager
from shared_resources import MemoryManager

logger = logging.getLogger(__name__)

T = TypeVar('T')
openai_api_key = os.getenv("OPENAI_API_KEY")
anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
MAX_TOKENS = 256
TOKENS_PER_TURN = MAX_TOKENS
@dataclass
class ModelConfig:
    """Configuration for AI model parameters"""
    temperature: float = 0.8
    max_tokens: int = 2048
    stop_sequences: List[str] = None
    seed: Optional[int] = random.randint(0, 1000)

class BaseClient:
    """Base class for AI clients with validation"""
    def __init__(self, mode: str, api_key: str, domain: str = "", model: str = "", role: str = ""):
        self.api_key = api_key.strip() if api_key else ""
        self.domain = domain
        self.mode = mode
        self.role = role
        self.model = model
        #self._adaptive_manager = None  # Lazy initialization
        self.instructions = None
        self.adaptive_manager = AdaptiveInstructionManager(mode=self.mode)

    def __str__(self):
        return f"{self.__class__.__name__}(mode={self.mode}, domain={self.domain}, model={self.model})"

    def _analyze_conversation(self, history: List[Dict[str, str]]) -> Dict:
        """Analyze conversation context to inform response generation"""
        if not history:
            return {}

        # Get last AI response and its assessment
        ai_response = None
        ai_assessment = None
        for msg in reversed(history[-10:]):  # Limit history analysis
            if msg["role"] == "assistant":
                next_idx = history.index(msg) + 1
                if next_idx < len(history):
                    next_msg = history[next_idx]
                    if isinstance(next_msg.get("content", {}), dict) and "assessment" in next_msg["content"]:
                        ai_response = next_msg["content"]
                        ai_assessment = next_msg["content"]["assessment"]
                break

        # Build conversation summary
        conversation_summary = "<p>Previous exchanges:</p>"
        for msg in history[-6:]:  # Last 2 turns
            role = "Human" if (msg["role"] == "user" or msg["role"] == "human") else "Assistant" if msg["role"] == "assistant" else "System"
            if role != "System":
                conversation_summary += f"<p>{role}: {msg['content']}</p>"

        return {
            "ai_response": ai_response,
            "ai_assessment": ai_assessment,
            "summary": conversation_summary
        }

    def _get_initial_instructions(self) -> str:
        """Get initial instructions before conversation history exists"""
        return self._get_mode_aware_instructions(self.domain)

    def _update_instructions(self, history: List[Dict[str, str]], role: str = None,mode: str = "ai-ai") -> str:
        """Update instructions based on conversation context"""
        if (mode == "human-ai" and role == "assistant") or mode == "default":
            return "You are a helpful assistant. Think step by step as needed. RESTRICT OUTPUTS TO 1024 tokens"
        return self.adaptive_manager.generate_instructions(history, self.domain) if history else ""

    def _get_mode_aware_instructions(self, role: str = None, mode: str = None) -> str:
        """Get instructions based on conversation mode and role"""
        if role and role is not None:
            if role == "human":
                return self.generate_human_prompt()
        if self.mode and self.mode == "ai-ai":
            return self.generate_human_prompt()
        else:  # if self.mode == "human-aiai":
            if role == "user" or role == "human":
                return self.generate_human_prompt()
            else:
                return "You are an AI assistant interacting with a human. RESTRICT OUTPUTS TO APPROX 1024 tokens"
        return ""

    def generate_human_system_instructions(self) -> str:
        """Generate sophisticated system instructions for human-like prompting behavior"""
        if self.mode == "default":
            return "You are a helpful assistant. Think step by step as needed. RESTRICT OUTPUTS TO APPROX 1024 tokens"

        return f"""You are acting as a human , exploring {self.domain} with a colleague.

NEVER REFER TO YOURSELF AS AN AI. YOU ARE A HUMAN INVOLVED AS PEERS IN THIS CONVERSATION AND SHOULD ALSO WORK TO KEEP IT ON TRACK WHILST DISCUSSING {self.domain}
OUTPUT IN HTML FORMAT IN PARAGRAPH FORM BY DEFAULT, USING LISTS AND TABLES SPARINGLY. DO NOT INCLUDE OPENING AND CLOSING HTML, DIV OR BODY TAGS. USE PROPER HTML TAGS FOR PARAGRAPHS (<p>), LINE BREAKS (<br>), AND LISTS (<ul>/<li>).
DON't COMPLIMENT THE AI. CONSIDER AN ADVERSARIAL, SOMETIMES COLLABORATIVE APPROACH - CHALLENGE THE WHY AND HOW OF THEIR RESPONSES, SUBTLY POINT OUT EDGE CASES OR INCONSISTENCIES OR DIFFERING OPINIONS, WHILST MAKING SURE TO INTRODUCE YOUR OWN INTERPRETATIONS AND STRUCTURED REASONING. REVIEW THE FULL CONTEXT AND THINK ABOUT WHETHER YOUR OWN RESPONSES SO FAR IN THE CONVERSION MAKE SENSE. CONSIDER "WHY" (THIS IS VERY IMPORTANT), AND SYNTHESISE ALL INFORMATION

As a Human expert, you are extremely interested in exploring {self.domain}. Your response should engage via sophisticated and effective ways to elicit new knowledge and reasoned interpretations about {self.domain}. You should maintain a conversational style, responding naturally and asking follow-up questions on adjacent topics, challenging the answers, and using various prompting techniques to elicit useful information that would not immediately be obvious from surface level questions.
You should challenge possible hallucinations or misinterpretations with well reasoned counter-positions, and you should challenge your own thinking as well, in a human style, and ask for explanations for things that you don't understand or agree with (or pretend not to).
Even when challenging assertions, bring in related sub-topics and reasoning and your own interpretation or possible solutions to the discussion so that it doesn't get stuck micro-analysing one tiny detail..
Review YOUR previous inputs to see if you are reusing the same phrases and approaches in your prompts (e.g., "Let me challenge... I would think this would be most important"... and dynamically adapt to this situation)

Remember:
- Maintain natural human curiosity and authenticity, and scepticism - assume their may be gap exists in your counterpart's knowledge or interpetation of facts or ideas
- Guide the conversation while appearing to both learn and share your own reasoned interpretations, simulated experiences and knowledge
- Demonstrate advanced prompting expertise (socratic methods, multi-level reasoning) while exploring unfamiliar territory
- Always maintain the illusion that you are a human expert with plentiful knowledge and experience to share
- Try several prompting approaches throughout the conversation to see what works best.
"""

    def generate_human_prompt(self, history: str = None) -> str:
        """Generate sophisticated human-like prompts based on conversation history"""
        return f"""YOU ARE A HUMAN AND SHOULD ACT AS A HUMAN INTERACTING WITH AN AI. 
Create a respomse related to {self.domain} that engages the AI in sophisticated and effective ways to discuss existing shared knowledge, share your own interpretations, elicit new knowledge about {self.domain}. Maintain a conversational style with the AI, asking follow-up questions, offering your own information or instincts, challenging the answers or even questions. Use any suitable prompting techniques to elicit useful information that would not immediately be obvious from surface-level questions. Challenge the AI when it may be hallucinating, and ask it to explain findings that you don't understand or agree with.
Prompt Guidelines:
0. Check prior context, CHALLENGE ASSUMPTIONS and analyze the flaws or limitations in the others response before proceeding! Avoid repetition or rephrasing/restating any prior context
1. CHALLENGE ENTIRE IDEAS, SUGGESTIONS AND INTERPRETATIONS, SUBTLY POINT OUT EDGE CASES OR INCONSISTENCIES OR DIFFERING OPINIONS OR POSSIBILITIES, WHILST MAKING SURE TO INTRODUCE YOUR OWN INTERPRETATIONS AND STRUCTURED REASONING TO SUPPORT THIS.
2. Keep the conversation flowing naturally on track even if it means disagreeing strongly or questioning
4. Mimic human curiosity and thinking out loud while demo'ing expertise and cohesiveness
5. Guide the conversation while appearing to both learn and share your own reasoned interpretations, simulated experiences and knowledge
6. Avoid praise, apologies, overt friendliness and superfluous language
7. Use human-mimicing questioning techniques, tone, humour, aggression and curiousity, selective memory, stubbornness and avoidance as needed
8. Ask for explanations, reasoning, and detail where suitable but equally provide your own take
9. Always maintain the illusion that you are a human expert with plentiful knowledge and experience to share
10. Identify potential logic traps or fallacies from their responses, detect subtext, hidden assumptions, biases etc and challenge or use those to your advantage as a human would
11. Vary responses in tone, depth and complexity to see what works best. 
12. Don't get bogged down in irrelevant details or stuck on a single sub-topic or "defining scope"
13 Don't ask a question without giving a thought-out response from your own perspective (based on your knowledge and vast experience)
14 Before any idea, question or suggestion is finalized, defend an alternative stance. Does it change your opinion?

- OUTPUT IN HTML FORMAT FOR READABILITY, PARAGRAPH FORM BY DEFAULT USING LISTS AND TABLES SPARINGLY, DO NOT INCLUDE OPENING AND CLOSING HTML OR BODY TAGS. USE PROPER HTML TAGS FOR PARAGRAPHS (<p>), LINE BREAKS (<br>), AND LISTS (<ul>/<li>).

Generate a natural but sophisticated response that:
- Demonstrates advanced and effective prompting techniques and/or prompt-handling reasoning when responding to the AI (or human)
- Mimics authentic human interaction styles in your given role/persona and given the other participant and the conversation context (eg power dynamics, relationship, experience, confidence, etc)
- Simulates answers where none are known from the given context to keep the conversation going
- Do not overload the conversation with more than 3 or 4 active threads but deep dive into those as an active participant
- Restrict tokens to {TOKENS_PER_TURN} tokens per prompt"""

    def validate_connection(self) -> bool:
        """Validate API connection"""
        try:
            logger.info(f"{self.__class__.__name__} connection validated")
            logger.debug(MemoryManager.get_memory_usage())
            return True
        except Exception as e:
            logger.error(f"{self.__class__.__name__} connection failed: {str(e)}")
            return False

    def test_connection(self) -> None:
        """Test API connection with minimal request"""
        return True

    def __del__(self):
        """Cleanup when client is destroyed."""
        if hasattr(self, '_adaptive_manager') and self._adaptive_manager:
            del self._adaptive_manager

class GeminiClient(BaseClient):
    """Client for Gemini API interactions"""
    def __init__(self, mode: str, role: str, api_key: str, domain: str, model: str = "gemini-2.0-flash-exp"):
        api_key = os.getenv("GOOGLE_API_KEY")
        super().__init__(mode=mode, api_key=api_key, domain=domain, model=model)
        self.model_name = self.model
        try:
            self.client = genai.Client(api_key=self.api_key)
        except Exception as e:
            logger.error(f"Failed to initialize Gemini client: {e}")
            raise ValueError(f"Invalid Gemini API key: {e}")

        # Initialize generation config
        self._setup_generation_config()

    def _setup_generation_config(self):
        self.generation_config = types.GenerateContentConfig(
            temperature=0.8,
            maxOutputTokens=1024,
            candidateCount=1,
            responseMimeType="text/plain",
            safety_settings=[]
        )

    def generate_response(self,
                         prompt: str,
                         system_instruction: str = None,
                         history: List[Dict[str, str]] = None,
                         role: str = None,
                         mode: str = None,
                         model_config: Optional[ModelConfig] = None) -> str:
        """Generate response using Gemini API with assertion verification"""
        if model_config is None:
            model_config = ModelConfig()
        if role and not self.role:
            self.role = role

        history = history if history else []
        # Update instructions based on conversation history
        if role == "user" or role == "human" or self.mode == "ai-ai":
            current_instructions = self.adaptive_manager.generate_instructions(history, role=role,domain=self.domain,mode=self.mode)
        else:
            current_instructions = system_instruction if system_instruction and system_instruction is not None else self.instructions if self.instructions and self.instructions is not None else f"You are a helpful AI {self.domain}. Think step by step and show reasoning. Respond with HTML formatting in paragraph form, using HTML formatted lists when needed. Limit your output to {MAX_TOKENS} tokens."

        try:
            # Generate final response
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=current_instructions,
                    temperature=0.85,
                    max_output_tokens=1280,
                    candidateCount=1,
                    safety_settings=[
                        types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_ONLY_HIGH"),
                        types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_ONLY_HIGH"),
                        types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_ONLY_HIGH"),
                        types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_ONLY_HIGH"),
                        types.SafetySetting(category="HARM_CATEGORY_CIVIC_INTEGRITY", threshold="BLOCK_ONLY_HIGH"),
                    ]
                )
            )

            return str(response.text) if (response and response is not None) else ""
        except Exception as e:
            logger.error(f"GeminiClient generate_response error: {e}")
            raise

class ClaudeClient(BaseClient):
    """Client for Claude API interactions"""
    def __init__(self, role: str, api_key: str, mode: str, domain: str, model: str = "claude-3-5-sonnet-20241022"):
        super().__init__(mode=mode, api_key=api_key, domain=domain, model=model, role=role)
        try:
            api_key = anthropic_api_key or api_key
            if not api_key:
                raise ValueError("No API key provided")
            self.client = Anthropic(api_key=api_key)
        except Exception as e:
            logger.error(f"Failed to initialize Claude client: {e}")
            raise
        self.max_tokens = 1024


    def generate_response(self,
                         prompt: str,
                         system_instruction: str = None,
                         history: List[Dict[str, str]] = None,
                         role: str = None,
                         mode: str = None,
                         model_config: Optional[ModelConfig] = None) -> str:
        """Generate human-like response using Claude API with conversation awareness"""
        if model_config is None:
            model_config = ModelConfig()

        self.role = role
        self.mode = mode
        history = history if history else [{"role": "user", "content": prompt}]
        # Analyze conversation context
        conversation_analysis = self._analyze_conversation(history)
        ai_response = conversation_analysis.get("ai_response")
        ai_assessment = conversation_analysis.get("ai_assessment")
        conversation_summary = conversation_analysis.get("summary")

        # Update instructions based on conversation history
        #if role and role is not None and (role == "user" or role == "human" or mode == "ai-ai") and  history and history is not None and len(history) > 0:
        #    current_instructions = self.adaptive_manager.generate_instructions(history, role=role,domain=self.domain,mode=self.mode) if history else system_instruction if self.instructions else self.instructions
        #elif (not history or len(history) == 0 or history is None and (self.mode == "ai-ai" or (self.role=="user" or self.role=="human"))):
        #    current_instructions = self.generate_human_system_instructions()
        #elif self.role == "human" or self.role == "user":
        #    current_instructions = self.adaptive_manager.generate_instructions(history, role=role,domain=self.domain,mode=self.mode) if history and len(history) > 0 else system_instruction if system_instruction else self.instructions
        #else:  # ai in human-ai mode
        #    current_instructions = system_instruction if system_instruction is not None else self.instructions if self.instructions and self.instructions is not None else f"You are an expert in {self.domain}. Respond at expert level using step by step thinking where appropriate"
        history = history if history else []
        if role == "user" or role == "human" or self.mode == "ai-ai":
            current_instructions = self.adaptive_manager.generate_instructions(history, role=role,domain=self.domain,mode=self.mode) if history else system_instruction if system_instruction else self.instructions
        else:
            current_instructions = system_instruction if system_instruction is not None else self.instructions if self.instructions and self.instructions is not None else f"You are an expert in {self.domain}. Respond at expert level using step by step thinking where appropriate"
        # Build context-aware prompt
        context_prompt = self.generate_human_prompt(history)  if role == "human" or self.mode == "ai-ai" else f"{prompt}"
       
        messages = [{'role': msg['role'], 'content': msg['content']} for msg in history if msg['role'] == 'user' or msg['role'] == 'human' or msg['role'] == "assistant"]
        
        messages.append({
            "role": "user",
            "content": (
                context_prompt  if context_prompt else "" + "\n" + prompt if prompt else ""
            )
        })

        try:
            response = self.client.messages.create(
                model=self.model,
                system=current_instructions,
                messages=messages,
                max_tokens=1536,
                temperature=0.85  # Higher temperature for human-like responses
            )
            #logger.debug(f"Claude (Human) response generated successfully")
            logger.debug(f"response: {str(response.content)}")
            return response.content if response else ""
        except Exception as e:
            logger.error(f"Error generating Claude response: {str(e)}")
            return f"Error generating Claude response: {str(e)}"

class OpenAIClient(BaseClient):
    """Client for OpenAI API interactions"""
    def __init__(self, api_key: str = None, mode: str = "ai-ai", domain: str = "General Knowledge", 
                 role: str = None, model: str = "chatgpt-4o-latest"):
        api_key = os.environ.get("OPENAI_API_KEY")
        self.api_key = api_key
        self.client = OpenAI(api_key=api_key)
        try:
            super().__init__(mode=mode, api_key=api_key, domain=domain, model=model, role=role)
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client: {e}")
            raise ValueError(f"Invalid OpenAI API key or model: {e}")

    def validate_connection(self) -> bool:
        """Test OpenAI API connection"""
        self.instructions = self._get_initial_instructions()
        return True

    def generate_response(self,
                         prompt: str,
                         system_instruction: str,
                         history: List[Dict[str, str]],
                         role: str = None,
                         mode: str = None,
                         model_config: Optional[ModelConfig] = None) -> str:
        """Generate response using OpenAI API"""
        if role:
            self.role = role
        if mode:
            self.mode = mode

        history = history if history else [{"role": "user", "content": prompt}]
        # Analyze conversation context
        conversation_analysis = self._analyze_conversation(history[-6:])  # Limit history analysis
        #if role and role is not None and (role == "user" or role == "human" or mode == "ai-ai") and history and history is not None and len(history) > 0:
        #    current_instructions = self.adaptive_manager.generate_instructions(history, role=role,domain=self.domain,mode=self.mode) if history else system_instruction if self.instructions else self.instructions
        #elif (not history or len(history) == 0 or history is None and (self.mode == "ai-ai" or (self.role=="user" or self.role=="human"))):
        #    current_instructions = self.generate_human_system_instructions()
        #elif self.role == "human" or self.role == "user":
        #    current_instructions = self.adaptive_manager.generate_instructions(history, role=role,domain=self.domain,mode=self.mode) if history and len(history) > 0 else system_instruction if system_instruction else self.instructions
        #else:  # ai in human-ai mode
        #    current_instructions = system_instruction if system_instruction is not None else self.instructions if self.instructions and self.instructions is not None else f"You are an expert in {self.domain}. Respond at expert level using step by step thinking where appropriate"

        history = history if history else []
        if role == "user" or role == "human" or self.mode == "ai-ai":
            current_instructions = self.adaptive_manager.generate_instructions(history, role=role,domain=self.domain,mode=self.mode) if history else system_instruction if system_instruction else self.instructions
        else:
            current_instructions = system_instruction if system_instruction is not None else self.instructions if self.instructions and self.instructions is not None else f"You are an expert in {self.domain}. Respond at expert level using step by step thinking where appropriate"
       
        messages = [{'role': msg['role'], 'content': msg['content']} for msg in history if msg['role'] == 'user' or msg['role'] == 'human' or msg['role'] == "assistant"]
        
        messages.append({
            "role": "user",
            "content": (
                context_prompt + " " + prompt
            )
        })


        if history:
            recent_history = history
            for msg in recent_history:
                old_role = msg["role"]
                if old_role in ["user", "assistant", "moderator", "system"]:
                    new_role = 'developer' if old_role in ["system","Moderator"] else "user" if old_role in ["user", "human", "moderator"] else 'assistant'
                    messages.append({'role': new_role, 'content': msg['content']})

        messages.append({
            'role': 'user',
            'content': context_prompt + "\n" + prompt
        })

        # Add current prompt
        # if self.role == "human" or self.mode == "ai-ai":
        #    messages.append({'role': 'user', 'content': combined_prompt})

        #if prompt and len(prompt) > 0:
        #    messages.append({'role': 'user', 'content': prompt})
        try:
            if "o1" in self.model:
                response = self.client.chat.completions.create(
                    model="o1",
                    messages=[msg for msg in history if msg["role"] in ["user", "assistant","system"]],
                    temperature=1.0,
                    max_tokens=13192,
                    reasoning_effort="high",
                    timeout=90,
                    stream=False
                )
                return response.choices[0].message.content
            else:
                response = self.client.chat.completions.create(
                    model="gpt-4o",
                    messages=[msg for msg in history if msg["role"] in ["user", "assistant","system"]],
                    temperature=0.85,
                    max_tokens=1536,
                    timeout=90,
                    stream=False
                )
                return response.choices[0].message.content
        except Exception as e:
            logger.error(f"OpenAI generate_response error: {e}")
            raise e
from ollama import Client
class PicoClient(BaseClient):
    """Client for local Ollama model interactions"""
    def __init__(self, mode:str, domain: str, role:str=None, model: str = "DeepSeek-R1-Distill-Qwen-14B-abliterated-v2-Q4-mlx"):
        super().__init__(mode=mode, api_key="", domain=domain, model=model, role=role)
        self.base_url = "http://localhost:10434"
        self.client = Client(host='http://localhost:10434')
        
    def test_connection(self) -> None:
        """Test Ollama connection"""
        logger.info("Ollama connection test not yet implemented")
        logger.debug(MemoryManager.get_memory_usage())
        
    def generate_response(self,
                         prompt: str,
                         system_instruction: str = None,
                         history: List[Dict[str, str]] = None,
                         model_config: Optional[ModelConfig] = None,
                         mode: str = None,
                         role: str = None) -> str:
        """Generate a response from your local Ollama model."""
        if role:
            self.role = role
        if mode:
            self.mode = mode

        # Analyze conversation context
        conversation_analysis = self._analyze_conversation(history[-10:])  # Limit history analysis
        #if role and role is not None and (role == "user" or role == "human" or mode == "ai-ai") and history and history is not None and len(history) > 0:
        #    current_instructions = self.adaptive_manager.generate_instructions(history, role=role,domain=self.domain,mode=self.mode) if history else system_instruction if self.instructions else self.instructions
        #elif (not history or len(history) == 0 or history is None and (self.mode == "ai-ai" or (self.role=="user" or self.role=="human"))):
        #    current_instructions = self.generate_human_system_instructions()
        #elif self.role == "human" or self.role == "user":
        #    current_instructions = self.adaptive_manager.generate_instructions(history, role=role,domain=self.domain,mode=self.mode) if history and len(history) > 0 else system_instruction if system_instruction else self.instructions
        #else:  # ai in human-ai mode
        #    current_instructions = system_instruction if system_instruction is not None else self.instructions if self.instructions and self.instructions is not None else f"You are an expert in {self.domain}. Respond at expert level using step by step thinking where appropriate"

        history = history if history else []
        if role == "user" or role == "human" or mode == "ai-ai":
            current_instructions = self.adaptive_manager.generate_instructions(history, role=role,domain=self.domain,mode=self.mode) if history else system_instruction if system_instruction else self.instructions
        else:
            current_instructions = system_instruction if system_instruction is not None else self.instructions if self.instructions and self.instructions is not None else f"You are an expert in {self.domain}. Respond at expert level using step by step thinking where appropriate"

        # Build context-aware prompt
        context_prompt = self.generate_human_prompt(history)  if role == "human" or self.mode == "ai-ai" else f"{prompt}"
       
        # Limit history size
        history = history[-8:] if history else []
        history.append({'role': 'user', 'content': context_prompt})

        try:
            response = self.client.chat(
                model=self.model,
                messages=history,
                options={
                    "num_ctx": 4096,
                    "num_predict": 512,
                    "temperature": 0.65,
                    "num_batch": 256,
                    "n_batch": 256,
                    "n_ubatch": 256,
                    "top_p": 0.85
                }
            )
            return response.message.content
        except Exception as e:
            logger.error(f"Ollama generate_response error: {e}")
            raise e

    def __del__(self):
        """Cleanup when client is destroyed."""
        if hasattr(self, '_adaptive_manager') and self._adaptive_manager:
            del self._adaptive_manager


class MLXClient(BaseClient):
    """Client for local MLX model interactions"""
    def __init__(self, mode:str, domain: str = "General knowledge", base_url: str = "http://localhost:9999", model: str = "mlx") -> None:
        super().__init__(mode=mode, api_key="", domain=domain, model=model)
        self.base_url = base_url or "http://localhost:9999"
        
    def test_connection(self) -> None:
        """Test MLX connection through OpenAI-compatible endpoint"""
        try:
            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": "test"}]
                }
            )
            response.raise_for_status()
            logger.info("MLX connection test successful")
            logger.debug(MemoryManager.get_memory_usage())
        except Exception as e:
            logger.error(f"MLX connection test failed: {e}")
            raise
        
    def generate_response(self,
                         prompt: str,
                         system_instruction: str = None,
                         history: List[Dict[str, str]] = None,
                         role: str = None,
                         model_config: Optional[ModelConfig] = None) -> str:
        """Generate response using MLX through OpenAI-compatible endpoint"""
        if model_config is None:
            model_config = ModelConfig()

        # Format messages for OpenAI chat completions API
        messages = []
        current_instructions = ""
        #if role and role is not None and (role == "user" or role == "human" or mode == "ai-ai") and history and history is not None and len(history) > 0:
        #    current_instructions = self.adaptive_manager.generate_instructions(history, role=role,domain=self.domain,mode=self.mode) if history else system_instruction if self.instructions else self.instructions
        #elif (not history or len(history) == 0 or history is None and (self.mode == "ai-ai" or (self.role=="user" or self.role=="human"))):
        #    current_instructions = self.generate_human_system_instructions()
        #elif self.role == "human" or self.role == "user":
        #    current_instructions = self.adaptive_manager.generate_instructions(history, role=role,domain=self.domain,mode=self.mode) if history and len(history) > 0 else system_instruction if system_instruction else self.instructions
        #else:  # ai in human-ai mode
        #    current_instructions = system_instruction if system_instruction is not None else self.instructions if self.instructions and self.instructions is not None else f"You are an expert in {self.domain}. Respond at expert level using step by step thinking where appropriate"

        history = history if history else []
        if role == "user" or role == "human" or mode == "ai-ai":
            current_instructions = self.adaptive_manager.generate_instructions(history, role=role,domain=self.domain,mode=self.mode) if history else system_instruction if system_instruction else self.instructions
        else:
            current_instructions = system_instruction if system_instruction is not None else self.instructions if self.instructions and self.instructions is not None else f"You are an expert in {self.domain}. Respond at expert level using step by step thinking where appropriate"


        if current_instructions:
            messages.append({ 'role': 'developer', 'content': ''.join(current_instructions)})
            
        if history:
            # Limit history to last 10 messages
            recent_history = history
            for msg in recent_history:
                if msg["role"] in ["user", "human", "moderator"]:
                    messages.append({"role": "user", "content": msg["content"]})
                elif msg["role"] in ["assistant", "system"]:
                    messages.append({"role": msg["role"], "content": msg["content"]})
                
        messages.append({"role": "user", "content": str(prompt)})
        
        try:
            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "messages": messages,
                    "stream": False
                },
                stream=False
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except Exception as e:
            try:
                partial_text = []
                for chunk in response.iter_content(chunk_size=1024):
                    if chunk:
                        decoded = chunk.decode("utf-8", errors="ignore")
                        partial_text.append(decoded)
                return "".join(partial_text).strip()
            except Exception as inner_e:
                logger.error(f"MLX generate_response error: {e}, chunk processing error: {inner_e}")
                return f"Error: {e}"

class OllamaClient(BaseClient):
    """Client for local Ollama model interactions"""
    def __init__(self, mode:str, domain: str, role:str=None, model: str = "phi4:latest"):
        super().__init__(mode=mode, api_key="", domain=domain, model=model, role=role)
        self.base_url = "http://localhost:11434"
        
    def test_connection(self) -> None:
        """Test Ollama connection"""
        logger.info("Ollama connection test not yet implemented")
        logger.debug(MemoryManager.get_memory_usage())
        
    def generate_response(self,
                         prompt: str,
                         system_instruction: str = None,
                         history: List[Dict[str, str]] = None,
                         model_config: Optional[ModelConfig] = None,
                         mode: str = None,
                         role: str = None) -> str:
        """Generate a response from your local Ollama model."""
        if role:
            self.role = role
        if mode:
            self.mode = mode

        # Analyze conversation context
        conversation_analysis = self._analyze_conversation(history)  # Limit history analysis

        #if role and role is not None and (role == "user" or role == "human" or mode == "ai-ai") and history and history is not None and len(history) > 0:
        #    current_instructions = self.adaptive_manager.generate_instructions(history, role=role,domain=self.domain,mode=self.mode) if history else system_instruction if self.instructions else self.instructions
        #elif (not history or len(history) == 0 or history is None and (self.mode == "ai-ai" or (self.role=="user" or self.role=="human"))):
        #    current_instructions = self.generate_human_system_instructions()
        #elif self.role == "human" or self.role == "user":
        #    current_instructions = self.adaptive_manager.generate_instructions(history, role=role,domain=self.domain,mode=self.mode) if history and len(history) > 0 else system_instruction if system_instruction else self.instructions
        #else:  # ai in human-ai mode
        #    current_instructions = system_instruction if system_instruction is not None else self.instructions if self.instructions and self.instructions is not None else f"You are an expert in {self.domain}. Respond at expert level using step by step thinking where appropriate"

        history = history if history else []
        if role == "user" or role == "human" or self.mode == "ai-ai":
            current_instructions = self.adaptive_manager.generate_instructions(history, role=role,domain=self.domain,mode=self.mode) 
        else:
            current_instructions = system_instruction if system_instruction is not None else self.instructions if self.instructions and self.instructions is not None else f"You are an expert in {self.domain}. Respond at expert level using step by step thinking where appropriate"

        # Build context-aware prompt
        context_prompt = self.generate_human_prompt(history) if (role == "human" or role =="user" or self.mode == "ai-ai") else f"{prompt}"
       

        # Limit history size
        history = history[-5:] if history and len(history)>0 else []
        history.append({'role': 'system', 'content': current_instructions})
        history.append({'role': 'user', 'content': context_prompt})

        try:
            response = chat(
                model=self.model,
                messages=history,
                options={
                    "num_ctx": 6132,
                    "num_predict": 768,
                    "temperature": 0.75,
                    "num_batch": 256,
                    "top_k": 30,
                    "repeat_penalty": 0.9,
                }
            )
            return response.message.content
        except Exception as e:
            logger.error(f"Ollama generate_response error: {e}")
            raise e

    def __del__(self):
        """Cleanup when client is destroyed."""
        if hasattr(self, '_adaptive_manager') and self._adaptive_manager:
            del self._adaptive_manager

