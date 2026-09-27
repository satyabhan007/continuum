# Multi-Harness Orchestrator - Simplified Version

## Overview

The Multi-Harness Orchestrator is a local tool designed to manage multiple agent harnesses (opencode, cline, antigravity, github_copilot, azure_openai) with seamless token management, context preservation, and automatic handoff capabilities. It ensures continuous execution across different agent harnesses even when token limits are exhausted.

## Key Features

### 🔄 **Automatic Harness Handoff**
- Detects token exhaustion and automatically transfers tasks to available harnesses
- Preserves complete execution context across handoffs
- Supports graceful degradation and load balancing

### 💾 **Context Persistence**
- Stores conversation history and execution state
- Supports recovery from failed executions
- Enables multi-session continuity

### 📊 **Token Management**
- Real-time token consumption monitoring
- Intelligent harness selection based on task complexity and token availability
- Cost optimization and budget tracking

### ⚡ **Intelligent Task Distribution**
- Task complexity analysis for harness selection
- Load balancing across multiple harnesses
- Performance monitoring and optimization

### 🛡️ **Fault Tolerance**
- Automatic recovery from token exhaustion
- Error context preservation
- Retry mechanisms with intelligent backoff ([design doc](docs/retry_with_backoff.md))

## System Architecture

```
Multi-Harness Orchestrator (Simplified)
├── Harness Pool (opencode, cline, antigravity, github_copilot, azure_openai)
├── Token Monitoring System
├── Context Persistence Layer
├── Task Execution Engine
├── Handoff Management System
└── Performance Analytics
```

## Quick Start

### 1. Installation

```bash
# Clone the repository
cgit clone <repository-url>
cd multi-harness-project

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install in development mode
pip install -e .
```

### 2. Running the Orchestrator

```bash
# Start the orchestrator
python multi_harness_orchestrator.py

# Or run as a module
python -m multi_harness_orchestrator
```

### 3. Running the Demonstration

```bash
python demo.py
```

## API Reference

### MultiHarnessOrchestrator Class

#### `__init__(config_path: str = "config/multi_harness_config.json")`

Initialize the orchestrator with configuration.

#### `async execute_task(task: str, preferences: Optional[Dict] = None) -> Dict`

Execute a task with automatic harness coordination and handoff.

**Parameters:**
- `task`: The task to execute
- `preferences`: Optional preferences for harness selection

**Returns:**
- `success`: Whether execution was successful
- `execution_id`: Unique identifier for the execution
- `result`: Execution result (if successful)
- `harness_used`: Harness that was used for execution
- `tokens_consumed`: Number of tokens consumed
- `execution_time`: Execution time in seconds

#### `select_harness(task: str, preferences: Optional[Dict] = None) -> str`

Select the best harness for a given task.

**Parameters:**
- `task`: The task to execute
- `preferences`: Optional preferences for harness selection

**Returns:**
- Harness name

#### `is_harness_available(harness_name: str) -> bool`

Check if a harness is available.

**Parameters:**
- `harness_name`: Name of the harness to check

**Returns:**
- True if harness is available, False otherwise

## Configuration

### Configuration File

The system uses `config/multi_harness_config.json` for configuration. Key settings include:

#### Harness Pool Configuration
- **name**: Harness identifier (opencode, cline, antigravity, github_copilot, azure_openai)
- **token_budget**: Total tokens available for this harness
- **models**: List of supported models
- **default_model**: Default model for this harness
- **cost_per_1k_tokens**: Cost per 1,000 tokens
- **max_concurrent_tasks**: Maximum concurrent tasks

#### Handoff Strategy
- **token_threshold_percent**: Percentage of token budget used to trigger handoff (default: 80%)
- **context_preservation**: Enable/disable context preservation during handoff
- **auto_recovery**: Enable/disable automatic recovery from token exhaustion
- **graceful_transfer**: Enable/disable graceful handoff transfer

#### Conversation Persistence
- **enabled**: Enable/disable conversation history storage
- **storage**: Storage mechanism (swarm_artifacts, database, file_system)
- **compaction_threshold**: Context window usage threshold for compaction (default: 80%)
- **max_history_size**: Maximum conversation history size

## Use Cases

### 1. **Production Workflow Automation**
- Automate complex multi-step workflows across different AI harnesses
- Ensure continuous operation despite token limitations
- Maintain context and state across long-running sessions

### 2. **Development and Testing**
- Test different AI models and APIs across multiple harnesses
- Compare performance and cost across different providers
- Validate handoff mechanisms and recovery procedures

### 3. **Research and Analysis**
- Process large datasets using distributed AI capabilities
- Analyze token usage patterns and optimize costs
- Research handoff strategies for different task types

### 4. **Educational Purposes**
- Learn about multi-agent coordination and orchestration
- Understand token management and cost optimization
- Explore advanced handoff and recovery mechanisms

## Development

### Running Tests

```bash
# Run unit tests
pytest tests/ -v
```

### Adding New Harnesses

To add a new harness, modify the `harness_pool` in the configuration file:

```json
{
  "name": "new_harness",
  "models": ["model1", "model2"],
  "token_budget": 50000,
  "default_model": "model1",
  "cost_per_1k_tokens": 0.001,
  "max_concurrent_tasks": 2,
  "api_endpoint": "https://api.example.com",
  "priority": 4
}
```

### Custom Handoff Strategies

Create custom handoff strategies by extending the `MultiHarnessOrchestrator` class and overriding:

- `select_harness()`: Custom harness selection logic
- `trigger_harness_handoff()`: Custom handoff mechanism
- `transfer_context_to_harness()`: Custom context transfer

## Requirements

### Python Version
- Python 3.6 or higher

### Dependencies
- python-dateutil>=2.8.0
- pydantic>=2.0.0

### Optional Dependencies
- redis>=4.0.0
- psutil>=5.9.0

## Project Structure

```
multi-harness-project/
├── multi_harness_orchestrator.py
│   └── Main orchestrator implementation
├── demo.py
│   └── Demonstration script
├── config/
│   └── multi_harness_config.json
│       └── Configuration file
├── README.md
│   └── Project documentation
├── requirements.txt
│   └── Dependencies
├── setup.py
│   └── Package configuration
└── __pycache__/
    └── *.pyc
    └── Cache files
```

## Testing

Run the demonstration to see the system in action:

```bash
python demo.py
```

The demonstration shows:
1. Basic task execution with automatic harness coordination
2. Intelligent harness selection based on task complexity
3. Token management and consumption tracking
4. Handoff simulation when token limits are reached

## Limitations

### Current Limitations

1. **Simplified Execution**: This version simulates task execution rather than integrating with real AI APIs
2. **Basic Token Estimation**: Token usage is estimated rather than actual API usage
3. **Limited Harness Support**: Currently supports a subset of agent harnesses

### Future Enhancements

1. **Real API Integration**: Integrate with actual OpenAI, Anthropic, Google, and other APIs
2. **Advanced Token Tracking**: Real token consumption tracking for actual API usage
3. **Expanded Harness Support**: Support for more agent harnesses and models
4. **Cloud Integration**: Integration with cloud platforms for distributed execution

## Troubleshooting

### Common Issues

#### Token Budget Exhaustion

**Issue**: All harnesses run out of tokens quickly.

**Solution**: Increase token budgets in configuration, add more harnesses, or optimize task distribution.

#### Harness Selection Problems

**Issue**: Tasks are not being assigned to appropriate harnesses.

**Solution**: Review task complexity analysis, check harness availability, and adjust handoff thresholds.

#### Performance Issues

**Issue**: The orchestrator is slow or unresponsive.

**Solution**: Check system resources, optimize configuration, and review performance settings.

## Getting Help

For issues and support:

1. **GitHub Issues**: Report bugs and request features
2. **Discussions**: Community discussion and support
3. **Documentation**: Check the README.md for detailed information
4. **Examples**: Run the demo script to see the system in action

## Community Guidelines

### Contributing

1. **Fork the repository**: Create your own copy
2. **Create issues**: Report bugs and suggest features
3. **Write tests**: Add unit and integration tests
4. **Improve documentation**: Enhance README and comments
5. **Submit pull requests**: Share your improvements

### Code Quality

1. **Follow PEP 8**: Adhere to Python coding standards
2. **Write clean code**: Keep code readable and maintainable
3. **Add documentation**: Document functions, classes, and modules
4. **Test thoroughly**: Ensure comprehensive test coverage
5. **Peer review**: Get feedback from other contributors

## Acknowledgments

The Multi-Harness Orchestrator builds upon and is inspired by:

- **AdalFlow**: Advanced agent framework for task execution
- **Jcode**: Agent orchestration and swarm management
- **OpenAI API**: Language model APIs
- **Anthropic API**: Claude language model APIs
- **Google AI**: Gemini language model APIs

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.

## Contact

For questions, issues, or contributions, please:

- **GitHub**: https://github.com/multi-harness/orchestrator
- **Email**: contact@example.com

## Support

This project is actively maintained and improved. Please report any issues and contribute to the project!
