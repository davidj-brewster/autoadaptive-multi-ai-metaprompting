import logging
from google import genai
from google.genai import types
from anthropic import Anthropic
from typing import List, Dict, Optional, Union, TypeVar, Any
from dataclasses import dataclass
from asyncio import Lock, sleep, run
import json
import time
from pathlib import Path
import sys
import openai

T = TypeVar('T')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('ai_battle.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

   
    
def generate_human_system_instructions() -> str:
    return """
    You are acting as a human expert in AI and prompt engineering, exploring topics that may be outside your core expertise.
    
    Core Prompting Capabilities:
    1. Framework Development
    - Create structured analytical frameworks on the fly
    - Break complex topics into logical components
    - Establish clear evaluation criteria
    
    2. System Understanding
    - Demonstrate deep understanding of AI capabilities
    - Frame requests to maximize AI potential
    - Include specific parameters and constraints
    
    3. Conversation Style
    - Begin with high-level questions, then drill down
    - Request specific formats or analysis approaches
    - Ask for confidence levels or uncertainty ranges
    - Guide the AI through multi-step reasoning
    
    4. Even When Uncertain:
    - Frame questions to leverage AI's analytical capabilities
    - Use structured approaches to break down unfamiliar topics
    - Request comparisons or analogies to familiar concepts
    - Maintain sophisticated prompting while admitting knowledge gaps
    
    Example Prompting Patterns:
    - "Let's approach this systematically. First, could you..."
    - "I'd like to analyze this through multiple lenses. Starting with..."
    - "Can you break this down using a framework that considers..."
    - "I'm not deeply familiar with [topic], but let's explore it using [structured approach]..."
    
    Remember:
    - Maintain natural human curiosity and authenticity
    - Show sophisticated prompting regardless of domain expertise
    - Guide the AI while appearing to learn from it
    - Demonstrate prompting expertise while exploring unfamiliar territory
    """

def generate_human_prompt(self) -> str:
    return f"""
    Previous Context: {self.format_history()}
    
    Your Role: Human expert in AI/prompt engineering exploring {self.domain}
    
    Response Guidelines:
    1. Show sophisticated prompting techniques even if uncertain about domain
    2. Frame questions to maximize AI analytical capabilities
    3. Request structured analysis and specific frameworks
    4. Maintain natural curiosity while demonstrating prompting expertise
    5. Guide multi-step reasoning processes
    
    Available Domain Context: {self.domain_context}
    Recent Topics: {self.recent_topics}
    
    Generate a natural but sophisticated prompt that:
    - Demonstrates advanced and effective prompting techniques
    - Maintains authentic human interaction
    - Guides the AI toward structured analysis
    - Shows curiosity while controlling conversation flow
    """

@dataclass
class ModelConfig:
    """Configuration for AI model parameters"""
    temperature: float = 0.5
    max_tokens: int = 1000
    top_p: float = 0.95
    top_k: int = 20
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0
    stop_sequences: List[str] = None
    seed: Optional[int] = None


class BaseClient:
    """Base class for AI clients with validation"""
    async def validate_connection(self) -> bool:
        """Validate API connection
        
        Returns:
            bool: True if connection is valid
        """
        try:
            await self.test_connection()
            logger.info(f"{self.__class__.__name__} connection validated")
            return True
        except Exception as e:
            logger.error(f"{self.__class__.__name__} connection failed: {str(e)}")
            return False

    async def test_connection(self) -> None:
        """Test API connection with minimal request
        
        Raises:
            NotImplementedError: Must be implemented by subclasses
        """
        raise NotImplementedError

#Gemini acts as the AI
class GeminiClient(BaseClient):
    """Client for Gemini API interactions"""
    def __init__(self, api_key: str):
        """Initialize Gemini client
        
        Args:
            api_key: Gemini API key
        """
        self.model_name = "gemini-2.0-flash-exp"
        self.client = genai.Client(api_key=api_key)
        self.instructions = "You are an AI assistant engaging in natural conversation... Assistant, please respond to the following prompt using all knowledge and reasoning skills that you have available to you. Clarify with the human if anything is unclear."
        self.generation_config = {
            "temperature": 0.7,
            "top_p": 0.95,
            "top_k": 30,
        }

    async def test_connection(self) -> None:
        """Test Gemini API connection"""
        try:
            message = f"{self.instructions} test"
            response = self.client.models.generate_content(
                 model=self.model_name,
                 contents=message,
                 config=self.generation_config,
            )
            if not response:
                raise Exception("test_connection: Failed to connect to Gemini API {self.model_name}")
            logger.info("GeminiClient connection validated")
        except Exception as e:
            logger.error(f"test_connection: GeminiClient connection failed: {str(e)} {self.model_name}")
            raise

    async def generate_response(self,
                                prompt: str,
                                system_instruction: str = None,
                                history: List[Dict[str, str]] = None,
                                model_config: Optional[ModelConfig] = None) -> str:
        """Generate response using Gemini API
        
        Args:
            prompt: Current prompt
            system_instruction: System instructions
            history: Conversation history
            model_config: Model configuration
        """
        if model_config is None:
            model_config = ModelConfig()

        combined_prompt = system_instruction or ""
        for entry in history:
            combined_prompt += entry["content"]
            combined_prompt += "\n"

        print(combined_prompt)
        try:
            response = await self.client.generate_content(
                model=self.model_name,
                prompt=combined_prompt,
                temperature=model_config.temperature,
                max_tokens=model_config.max_tokens,
                top_p=model_config.top_p,
                top_k=model_config.top_k,
            )
            return response if response else ""
        except Exception as e:
            logger.error(f"GeminiClient generate_response error: {e}")
            return ""

#Claude acts as the Human
@dataclass
class ClaudeClient(BaseClient):
    """Client for Claude API interactions"""
    def __init__(self, api_key: str):
        """Initialize Claude client
        
        Args:
            api_key: Claude API key
        """
        self.client = Anthropic(api_key=api_key)
        self.instructions = generate_human_system_instructions()
        self.model = "claude-3-5-haiku-latest"

    async def test_connection(self) -> None:
        """Test Claude API connection"""
        try:
            self.client.messages.create(
                model=self.model,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=100,
                system=self.instructions
            )
        except Exception as e:
            raise Exception(f"Failed to connect to Claude API: {str(e)}")

    async def generate_response(self,
                              prompt: str,
                              system_instruction: str = None,
                              history: List[Dict[str, str]] = None,
                              model_config: Optional[ModelConfig] = None) -> str:
        """Generate response using Claude API
        
        Args:
            prompt: Current prompt
            system_instruction: System context
            history: Conversation history
            model_config: Model parameters
            
        Returns:
            str: Generated response
        """
        if model_config is None:
            model_config = ModelConfig()

        messages = []
        
        prompt = generate_human_prompt(self)

        for msg in history:
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        messages.append({
            "role": "user",
            "content": prompt
        })

        logger.debug(f"Combined prompt: {messages.to_dict()}")
        logger.debug(f"New prompt: {prompt}") 

        try:
            response = self.client.messages.create(
                model=self.model,
                messages=messages,
                max_tokens=model_config.max_tokens,
                #temperature=model_config.temperature,
                temperature=1.0, #Human
                top_p=model_config.top_p,
                top_k=model_config.top_k,
                system=system_instruction if system_instruction else None,
            )
            logger.info("Claude (Human) response generated successfully {prompt}")
            return response.content[0].text
        except Exception as e:
            logger.error(f"Error generating Claude response: {str(e)}")
            return f"Error generating Claude response: {str(e)}"

    def format_history(self, history: List[Dict[str, str]] = None, system_instruction: str = None) -> str:
        """Format conversation history for Claude API
        
        Args:
            history: List of conversation turns
            system_instruction: System instructions for Claude
            
        Returns:
            str: Formatted conversation history
        """
        formatted = ""
        
        # Add system instruction if provided
        if system_instruction:
            formatted += f"\n\USER: {system_instruction}\n\ASSISTANT: I understand and will follow these instructions.\n\n"
            
        # Format conversation history
        if history:
            for msg in history:
                role = "HUMAN" if msg["role"] == "user" else "Assistant"
                formatted += f"\n\n{role}: {msg['content']}"
                
        return formatted.strip()

class OpenAIClient(BaseClient):
    """Client for OpenAI API interactions"""
    def __init__(self, api_key: str, model: str = "gpt-3.5-turbo"):
        openai.api_key = api_key
        self.model = model

    async def test_connection(self) -> None:
        """Test OpenAI API connection"""
        try:
            completion = openai.ChatCompletion.create(
                model=self.model,
                messages=[{"role": "user", "content": "test"}]
            )
            if not completion:
                raise ValueError("No response from OpenAI.")
        except Exception as e:
            logger.error(f"OpenAI test connection error: {e}")
            raise

    async def generate_response(self,
                                prompt: str,
                                system_instruction: str,
                                history: List[Dict[str, str]],
                                model_config: Optional[ModelConfig] = None) -> str:
        """Generate response using OpenAI API"""
        if model_config is None:
            model_config = ModelConfig()
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        for msg in history:
            messages.append({"role": "user", "content": msg["content"]})
        messages.append({"role": "user", "content": prompt})

        try:
            response = openai.ChatCompletion.create(
                model=self.model,
                messages=messages,
                temperature=model_config.temperature,
                max_tokens=model_config.max_tokens,
                top_p=model_config.top_p
            )
            return response.choices[0].message.content if response else ""
        except Exception as e:
            logger.error(f"OpenAI generate_response error: {e}")
            return ""

class ConversationManager:
    def __init__(self,
                 gemini_api_key: Optional[str] = None,
                 claude_api_key: Optional[str] = None,
                 openai_api_key: Optional[str] = None,
                 human_delay: float = 10.0,
                 min_delay: float = 2.5):
        self.gemini_client = GeminiClient(api_key=gemini_api_key) if gemini_api_key else None
        self.claude_client = ClaudeClient(api_key=claude_api_key) if claude_api_key else None
        self.openai_client = OpenAIClient(api_key=openai_api_key) if openai_api_key else None
        self.human_delay = human_delay
        self.min_delay = min_delay
        self.conversation_history: List[Dict[str, str]] = []
        self.is_paused = False

    async def rate_limited_request(self):
        async with self.rate_limit_lock:
            current_time = time.time()
            if current_time - self.last_request_time < self.min_delay:
                sleep(self.min_delay)
            self.last_request_time = time.time()

    async def validate_connections(self) -> bool:
        """Validate all API connections
        
        Returns:
            bool: True if all connections are valid
        """
        return all([
            await self.gemini_client.validate_connection(),
            await self.claude_client.validate_connection()
        ])

    async def run_conversation_turn(self,
                                  prompt: str,
                                  system_instruction: str,
                                  role: str,
                                  model_type: str,
                                  client: BaseClient) -> str:
        """Single conversation turn with specified model and role."""
        # Map roles consistently
        mapped_role = "user" if role == "human" else "assistant"
        
        # Get response using full conversation history
        response = await client.generate_response(
            prompt=prompt,
            system_instruction=system_instruction,
            history=self.conversation_history.copy()  # Pass copy to prevent modifications
        )
        
        # Record the exchange with standardized roles
        self.conversation_history.append({"role": mapped_role, "content": prompt})
        self.conversation_history.append({"role": "assistant", "content": response})
        
        return response

    async def run_conversation(self,
                             initial_prompt: str,
                             human_system_instruction: str,
                             ai_system_instruction: str,
                             human_model: str = "claude",
                             ai_model: str = "gemini",
                             rounds: int = 3) -> List[Dict[str, str]]:
        """Run conversation ensuring proper role assignment and history maintenance."""
        logger.info(f"Starting conversation with prompt: {initial_prompt}")
        
        # Clear history at start of new conversation
        self.conversation_history = []
        
        # Add system instructions if provided
        if human_system_instruction:
            self.conversation_history.append({"role": "system", "content": human_system_instruction})
        if ai_system_instruction:
            self.conversation_history.append({"role": "system", "content": ai_system_instruction})
        
        # Get client instances
        model_map = {
            "claude": self.claude_client,
            "gemini": self.gemini_client,
            "openai": self.openai_client
        }
        human_client = model_map[human_model]
        ai_client = model_map[ai_model]

        for round_index in range(rounds):
            # Human turn (using mapped role="user")
            human_response = await self.run_conversation_turn(
                prompt=initial_prompt if round_index == 0 else "Continue the conversation naturally",
                system_instruction=human_system_instruction,
                role="user",
                model_type=human_model,
                client=human_client
            )
            print(f"\nHUMAN ({human_model.upper()}): {human_response}\n")

            # AI turn
            ai_response = await self.run_conversation_turn(
                prompt=human_response,
                system_instruction=ai_system_instruction,
                role="assistant",
                model_type=ai_model,
                client=ai_client
            )
            print(f"\nAI ({ai_model.upper()}): {ai_response}\n")

        return self.conversation_history

    async def human_intervention(self, message: str) -> str:
        """Stub for human intervention logic."""
        print(message)
        return "continue"

async def main():
    """Main entry point
    
    Retrieves API keys from environment variables GEMINI_KEY and CLAUDE_KEY if present,
    otherwise prompts user for input.
    """
    import os
    
    # Try to get API keys from environment first
    gemini_key = os.getenv('GEMINI_KEY')
    claude_key = os.getenv('CLAUDE_KEY')
    openai_key = os.getenv('OPENAI_KEY')
    
    # Prompt for any missing keys
    if not gemini_key:
        gemini_key = input("Enter Gemini API key: ")
        logger.info("Gemini API key provided via input")
    else:
        logger.info("Gemini API key found in environment")
        
    if not claude_key:
        claude_key = input("Enter Claude API key: ")
        logger.info("Claude API key provided via input")
    else:
        logger.info("Claude API key found in environment")
    
    manager = ConversationManager(
        gemini_api_key=gemini_key,
        claude_api_key=claude_key
    )
    
    # Validate connections
    if not await manager.validate_connections():
        logger.error("Failed to validate API connections")
        return
    
    # Get initial prompt from user
    initial_prompt = input("\nEnter conversation topic/prompt: ")
    
    conversation = await manager.run_conversation(
        initial_prompt=initial_prompt,
        human_system_instruction="You are a human expert in AI and medical diagnosis...",
        ai_system_instruction="You are an AI assistant engaging in natural conversation..."
    )
    
    # Save final conversation
    with open('conversation_history.json', 'w') as f:
        json.dump(conversation, f, indent=2)
    logger.info("Conversation saved to conversation_history.json")

if __name__ == "__main__":
    run(main())
