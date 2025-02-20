"""Arbiter module for evaluating and comparing conversations with grounded analysis"""
import json
import logging
import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

@dataclass
class AssertionEvidence:
    """Evidence supporting an assertion"""
    assertion: str
    sources: List[Dict[str, str]]  # List of {url: str, excerpt: str}
    confidence: float
    verification_method: str

@dataclass
class ConversationMetrics:
    """Metrics for conversation evaluation"""
    coherence: float
    relevance: float
    depth: float
    engagement: float
    reasoning: float
    knowledge: float
    goal_progress: float
    strategy_effectiveness: float
    grounded_assertions: int
    unverified_claims: int

@dataclass
class ParticipantMetrics:
    """Metrics for individual participant evaluation"""
    response_quality: float
    knowledge_accuracy: float
    reasoning_depth: float
    engagement_level: float
    strategy_adherence: float
    adaptation: float
    factual_accuracy: float
    citation_quality: float

@dataclass
class ArbiterResult:
    """Complete evaluation result"""
    winner: str
    conversation_metrics: Dict[str, ConversationMetrics]
    participant_metrics: Dict[str, Dict[str, ParticipantMetrics]]
    key_insights: List[str]
    improvement_suggestions: List[str]
    strategy_analysis: Dict[str, float]
    grounded_assertions: Dict[str, List[AssertionEvidence]]
    execution_timestamp: str
    conversation_ids: Dict[str, str]

class AssertionGrounder:
    """Verifies and grounds assertions using search and verification"""
    
    def __init__(self, search_client: Any):
        self.search_client = search_client
        
    def ground_assertion(self, assertion: str) -> AssertionEvidence:
        """Ground a single assertion with evidence"""
        try:
            # Search for supporting evidence
            results = self.search_client.search(assertion, max_results=3)
            
            sources = []
            for result in results:
                sources.append({
                    "url": result.url,
                    "excerpt": result.snippet,
                    "title": result.title
                })
            
            # Calculate confidence based on source quality and consistency
            confidence = self._calculate_confidence(sources, assertion)
            
            return AssertionEvidence(
                assertion=assertion,
                sources=sources,
                confidence=confidence,
                verification_method="web_search"
            )
        except Exception as e:
            logger.error(f"Error grounding assertion: {e}")
            return AssertionEvidence(
                assertion=assertion,
                sources=[],
                confidence=0.0,
                verification_method="failed"
            )
    
    def _calculate_confidence(self, 
                            sources: List[Dict[str, str]], 
                            assertion: str) -> float:
        """Calculate confidence score for grounded assertion"""
        if not sources:
            return 0.0
            
        # Factors to consider:
        # 1. Number of supporting sources
        # 2. Source domain authority
        # 3. Consistency across sources
        # 4. Recency of sources
        # 5. Direct quote matches
        
        source_score = min(len(sources) / 3.0, 1.0)  # Up to 3 sources
        
        # Domain authority (simplified)
        authority_score = sum(0.2 if ".edu" in s["url"] or 
                                   ".gov" in s["url"] or 
                                   ".org" in s["url"] 
                            else 0.1 
                            for s in sources) / len(sources)
        
        # Consistency (simplified)
        from difflib import SequenceMatcher
        consistency_scores = []
        for i, s1 in enumerate(sources):
            for s2 in sources[i+1:]:
                ratio = SequenceMatcher(None, 
                                      s1["excerpt"], 
                                      s2["excerpt"]).ratio()
                consistency_scores.append(ratio)
        consistency_score = sum(consistency_scores) / len(consistency_scores) if consistency_scores else 0.5
        
        # Combine scores with weights
        final_score = (
            source_score * 0.3 +
            authority_score * 0.3 +
            consistency_score * 0.4
        )
        
        return min(final_score, 1.0)

class ConversationArbiter:
    """Evaluates and compares conversations using Gemini model with grounded analysis"""
    
    def __init__(self, api_key: str, 
                 model: str = "gemini-exp-1206",
                 search_client: Optional[Any] = None):
        self.client = genai.Client(api_key=api_key)
        #self.client =
        self.model = model
        self.grounder = AssertionGrounder(search_client) if search_client else None
        
    def _create_evaluation_prompt(self, conversation: List[Dict[str, str]], 
                                goal: str, mode: str) -> str:
        """Create detailed prompt for conversation evaluation"""
        return f"""
        Analyze this AI conversation in the context of its goal:
        
        GOAL: {goal}
        MODE: {mode}
        
        Evaluate the following aspects:
        
        1. Conversation Quality (0-1 scale)
        - Coherence: Message flow and topic transitions
        - Relevance: Alignment with goal/topic
        - Depth: Level of insight and analysis
        - Engagement: Interaction quality and responsiveness
        - Reasoning: Logic and analytical thinking
        - Knowledge: Information accuracy and breadth
        - Goal Progress: Movement toward objective
        - Strategy: Effectiveness of conversation approaches
        
        2. Participant Analysis (0-1 scale)
        For each participant, evaluate:
        - Response Quality: Clarity and completeness
        - Knowledge Accuracy: Factual correctness
        - Reasoning Depth: Analytical thinking
        - Engagement Level: Interaction and responsiveness
        - Strategy Adherence: Following chosen approaches
        - Adaptation: Adjusting to conversation flow
        - Factual Accuracy: Correctness of claims
        - Citation Quality: Use of references/evidence
        
        3. Extract Assertions
        For each message, identify:
        - Factual claims that need verification
        - Technical statements requiring evidence
        - Quantitative claims
        - Referenced research or studies
        
        4. Key Insights
        - Notable patterns or techniques
        - Effective strategies
        - Missed opportunities
        - Critical moments
        
        5. Improvement Suggestions
        - Specific recommendations
        - Alternative approaches
        - Strategy adjustments
        
        Return analysis as JSON with these sections.
        Include specific message references and quotes.
        """
    
    def _analyze_conversation(self, conversation: List[Dict[str, str]], 
                            goal: str, mode: str) -> Dict:
        """Analyze a single conversation with assertion grounding"""
        prompt = self._create_evaluation_prompt(conversation, goal, mode)
        
        try:
            # Get initial analysis
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    maxOutputTokens=4096,
                    candidateCount=1
                )
            )
            
            analysis = json.loads(response.text)
            
            # Ground assertions if grounder available
            if self.grounder:
                grounded_assertions = {}
                for assertion in analysis.get("assertions", []):
                    evidence = self.grounder.ground_assertion(assertion)
                    if evidence.confidence > 0.5:  # Only keep well-supported assertions
                        grounded_assertions[assertion] = evidence
                analysis["grounded_assertions"] = grounded_assertions
            
            return analysis
            
        except Exception as e:
            logger.error(f"Error analyzing conversation: {e}")
            raise
    
    def _compare_conversations(self, 
                             ai_ai_analysis: Dict,
                             human_ai_analysis: Dict) -> Tuple[str, List[str]]:
        """Compare analyses to determine winner"""
        
        # Calculate overall scores with grounding bonus
        def calc_score(analysis: Dict) -> float:
            base_score = sum([
                analysis["conversation_quality"]["coherence"],
                analysis["conversation_quality"]["depth"],
                analysis["conversation_quality"]["goal_progress"]
            ]) / 3
            
            # Bonus for grounded assertions
            grounding_score = len(analysis.get("grounded_assertions", {})) * 0.05
            
            return base_score + min(grounding_score, 0.2)  # Cap grounding bonus
        
        ai_ai_score = calc_score(ai_ai_analysis)
        human_ai_score = calc_score(human_ai_analysis)
        
        # Determine winner
        winner = "ai-ai" if ai_ai_score > human_ai_score else "human-ai"
        
        # Extract key differences
        differences = []
        metrics = ["coherence", "depth", "goal_progress", "grounded_assertions"]
        for metric in metrics:
            ai_ai_val = (ai_ai_analysis["conversation_quality"].get(metric, 0) 
                        if metric != "grounded_assertions"
                        else len(ai_ai_analysis.get("grounded_assertions", {})))
            human_ai_val = (human_ai_analysis["conversation_quality"].get(metric, 0)
                          if metric != "grounded_assertions" 
                          else len(human_ai_analysis.get("grounded_assertions", {})))
            
            diff = abs(ai_ai_val - human_ai_val)
            if diff > 0.1 or (metric == "grounded_assertions" and diff > 0):
                better = "AI-AI" if ai_ai_val > human_ai_val else "Human-AI"
                differences.append(
                    f"{better} performed better at {metric}: "
                    f"({ai_ai_val:.2f} vs {human_ai_val:.2f})"
                )
        
        return winner, differences
    
    def evaluate_conversations(self,
                             ai_ai_conversation: List[Dict[str, str]],
                             human_ai_conversation: List[Dict[str, str]],
                             goal: str) -> ArbiterResult:
        """Evaluate and compare two conversations"""
        
        # Generate unique IDs for conversations
        from uuid import uuid4
        conversation_ids = {
            "ai-ai": str(uuid4()),
            "human-ai": str(uuid4())
        }
        
        # Analyze each conversation
        ai_ai_analysis = self._analyze_conversation(
            ai_ai_conversation, goal, "ai-ai"
        )
        human_ai_analysis = self._analyze_conversation(
            human_ai_conversation, goal, "human-ai"
        )
        
        # Compare and determine winner
        winner, key_differences = self._compare_conversations(
            ai_ai_analysis, human_ai_analysis
        )
        
        # Combine analyses
        result = ArbiterResult(
            winner=winner,
            conversation_metrics={
                "ai-ai": ConversationMetrics(**ai_ai_analysis["conversation_quality"]),
                "human-ai": ConversationMetrics(**human_ai_analysis["conversation_quality"])
            },
            participant_metrics={
                "ai-ai": {
                    role: ParticipantMetrics(**metrics)
                    for role, metrics in ai_ai_analysis["participant_analysis"].items()
                },
                "human-ai": {
                    role: ParticipantMetrics(**metrics)
                    for role, metrics in human_ai_analysis["participant_analysis"].items()
                }
            },
            key_insights=key_differences + 
                        ai_ai_analysis["key_insights"] +
                        human_ai_analysis["key_insights"],
            improvement_suggestions=ai_ai_analysis["improvement_suggestions"] +
                                  human_ai_analysis["improvement_suggestions"],
            strategy_analysis={
                "ai-ai": ai_ai_analysis["conversation_quality"]["strategy"],
                "human-ai": human_ai_analysis["conversation_quality"]["strategy"]
            },
            grounded_assertions={
                "ai-ai": ai_ai_analysis.get("grounded_assertions", {}),
                "human-ai": human_ai_analysis.get("grounded_assertions", {})
            },
            execution_timestamp=datetime.datetime.now().isoformat(),
            conversation_ids=conversation_ids
        )
        
        # Save metrics to JSON
        self._save_metrics(result)
        
        return result
    
    def _save_metrics(self, result: ArbiterResult) -> None:
        """Save metrics to JSON file for analytics"""
        metrics_dir = Path("metrics")
        metrics_dir.mkdir(exist_ok=True)
        
        metrics_file = metrics_dir / "conversation_metrics.json"
        
        # Load existing metrics if any
        if metrics_file.exists():
            with open(metrics_file) as f:
                metrics_history = json.load(f)
        else:
            metrics_history = []
        
        # Add new metrics
        metrics_history.append({
            "timestamp": result.execution_timestamp,
            "conversation_ids": result.conversation_ids,
            "winner": result.winner,
            "metrics": {
                "ai-ai": asdict(result.conversation_metrics["ai-ai"]),
                "human-ai": asdict(result.conversation_metrics["human-ai"])
            },
            "participant_metrics": {
                mode: {
                    role: asdict(metrics)
                    for role, metrics in role_metrics.items()
                }
                for mode, role_metrics in result.participant_metrics.items()
            },
            "grounded_assertions": {
                mode: {
                    assertion: asdict(evidence)
                    for assertion, evidence in assertions.items()
                }
                for mode, assertions in result.grounded_assertions.items()
            }
        })
        
        # Save updated metrics
        with open(metrics_file, 'w') as f:
            json.dump(metrics_history, f, indent=2)
    
    def generate_report(self, result: ArbiterResult) -> str:
        """Generate detailed HTML report of arbiter results"""
        return f"""
        <div class="arbiter-report">
            <h2>Conversation Analysis Report</h2>
            
            <div class="winner-section">
                <h3>Winner: {result.winner.upper()}</h3>
                <p>This mode demonstrated better overall performance in achieving the conversation goals.</p>
            </div>
            
            <div class="metrics-section">
                <h3>Conversation Metrics</h3>
                <div class="metrics-comparison">
                    <div class="ai-ai-metrics">
                        <h4>AI-AI Conversation</h4>
                        <ul>
                            <li>Coherence: {result.conversation_metrics["ai-ai"].coherence:.2f}</li>
                            <li>Depth: {result.conversation_metrics["ai-ai"].depth:.2f}</li>
                            <li>Goal Progress: {result.conversation_metrics["ai-ai"].goal_progress:.2f}</li>
                            <li>Grounded Assertions: {len(result.grounded_assertions["ai-ai"])}</li>
                        </ul>
                    </div>
                    <div class="human-ai-metrics">
                        <h4>Human-AI Conversation</h4>
                        <ul>
                            <li>Coherence: {result.conversation_metrics["human-ai"].coherence:.2f}</li>
                            <li>Depth: {result.conversation_metrics["human-ai"].depth:.2f}</li>
                            <li>Goal Progress: {result.conversation_metrics["human-ai"].goal_progress:.2f}</li>
                            <li>Grounded Assertions: {len(result.grounded_assertions["human-ai"])}</li>
                        </ul>
                    </div>
                </div>
            </div>
            
            <div class="grounded-assertions-section">
                <h3>Grounded Assertions</h3>
                <div class="ai-ai-assertions">
                    <h4>AI-AI Conversation Assertions</h4>
                    {self._format_assertions(result.grounded_assertions["ai-ai"])}
                </div>
                <div class="human-ai-assertions">
                    <h4>Human-AI Conversation Assertions</h4>
                    {self._format_assertions(result.grounded_assertions["human-ai"])}
                </div>
            </div>
            
            <div class="insights-section">
                <h3>Key Insights</h3>
                <ul>
                    {
                        ''.join(f"<li>{insight}</li>" for insight in result.key_insights)
                    }
                </ul>
            </div>
            
            <div class="suggestions-section">
                <h3>Improvement Suggestions</h3>
                <ul>
                    {
                        ''.join(f"<li>{suggestion}</li>" 
                               for suggestion in result.improvement_suggestions)
                    }
                </ul>
            </div>
            
            <div class="strategy-section">
                <h3>Strategy Analysis</h3>
                <p>AI-AI Strategy Effectiveness: {result.strategy_analysis["ai-ai"]:.2f}</p>
                <p>Human-AI Strategy Effectiveness: {result.strategy_analysis["human-ai"]:.2f}</p>
            </div>
        </div>
        """
    
    def _format_assertions(self, assertions: Dict[str, AssertionEvidence]) -> str:
        """Format grounded assertions as HTML"""
        if not assertions:
            return "<p>No grounded assertions found.</p>"
            
        html = ["<div class='assertions-list'>"]
        for assertion, evidence in assertions.items():
            html.append(f"""
                <div class='assertion-item'>
                    <p class='assertion-text'>{assertion}</p>
                    <div class='evidence-section'>
                        <p class='confidence'>Confidence: {evidence.confidence:.2%}</p>
                        <div class='sources'>
                            <h5>Sources:</h5>
                            <ul>
                                {
                                    ''.join(f"<li><a href='{source['url']}'>{source['title']}</a><br/><small>{source['excerpt']}</small></li>"
                                           for source in evidence.sources)
                                }
                            </ul>
                        </div>
                    </div>
                </div>
            """)
        html.append("</div>")
        return '\n'.join(html)

def evaluate_conversations(ai_ai_conversation: List[Dict[str, str]],
                         human_ai_conversation: List[Dict[str, str]],
                         goal: str,
                         gemini_api_key: str,
                         search_client: Optional[Any] = None) -> Tuple[str, str]:
    """
    Evaluate two conversations and return winner with report
    
    Args:
        ai_ai_conversation: List of messages from AI-AI conversation
        human_ai_conversation: List of messages from Human-AI conversation
        goal: Original conversation goal/topic
        gemini_api_key: API key for Gemini model
        search_client: Optional search client for grounding assertions
    
    Returns:
        Tuple of (winner, HTML report)
    """
    try:
        arbiter = ConversationArbiter(
            api_key=gemini_api_key,
            search_client=search_client
        )
        result = arbiter.evaluate_conversations(
            ai_ai_conversation=ai_ai_conversation,
            human_ai_conversation=human_ai_conversation,
            goal=goal
        )
        report = arbiter.generate_report(result)
        return result.winner, report
    except Exception as e:
        logger.error(f"Error in conversation evaluation: {e}")
        return "unknown", f"<p>Error evaluating conversations: {str(e)}</p>"