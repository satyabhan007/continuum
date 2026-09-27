# Multi-Harness Orchestrator - Simplified Version Dependencies

## Required Dependencies

```
python-dateutil>=2.8.0
pydantic>=2.0.0
asyncio
logging
json
"""

## Optional Dependencies (for enhanced functionality)

```
redis>=4.0.0
psutil>=5.9.0
"""

## Installation

### Basic Installation

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

### Usage

```bash
# Start the orchestrator
python multi_harness_orchestrator.py

# Run the demonstration
python demo.py
```

## System Requirements

- Python 3.6 or higher
- 2+ GB RAM (recommended)
- 500+ MB disk space
- Internet connection (for API integrations)

## Quick Start

### 1. Setup

```bash
# Clone and setup
cgit clone <repository-url>
cd multi-harness-project
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Run Demonstration

```bash
python demo.py
```

### 3. Start Orchestrator

```bash
python multi_harness_orchestrator.py
```

## Features Demonstrated

### ✅ Core Features

1. **Harness Coordination**: Manages multiple agent harnesses (opencode, cline, antigravity)
2. **Token Management**: Tracks token consumption and enforces budget limits
3. **Context Preservation**: Maintains execution state across tasks and handoffs
4. **Intelligent Handoff**: Automatically transfers tasks when token limits are reached
5. **Load Balancing**: Distributes tasks based on harness availability and complexity

### ✅ Technical Features

1. **Real-time Monitoring**: Tracks token usage, execution performance, and system resources
2. **Error Recovery**: Automatic recovery from failed executions and token exhaustion
3. **Flexible Configuration**: Customizable harness settings and handoff strategies
4. **Scalable Architecture**: Supports adding new harnesses and models dynamically
5. **Comprehensive Logging**: Detailed logging for debugging and monitoring

## Architecture Overview

```
Multi-Harness Orchestrator (Simplified)
├── Harness Management
│   ├── opencode (100K tokens)
│   ├── cline (80K tokens)
│   └── antigravity (60K tokens)
├── Token Monitoring System
│   ├── Real-time token tracking
│   ├── Budget enforcement
│   └── Consumption analytics
├── Context Persistence Layer
│   ├── Execution state storage
│   ├── Conversation history
│   └── Recovery management
├── Task Execution Engine
│   ├── Intelligent harness selection
│   ├── Task complexity analysis
│   └── Performance optimization
└── Handoff Management System
    ├── Automatic handoff triggers
    ├── Context transfer
    └── Load balancing
```

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

## Limitations and Considerations

### Current Limitations

1. **Simplified Execution**: This version simulates task execution rather than integrating with real AI APIs
2. **Basic Token Estimation**: Token usage is estimated rather than actual API usage
3. **Limited Harness Support**: Currently supports a subset of agent harnesses

### Future Enhancements

1. **Real API Integration**: Integrate with actual OpenAI, Anthropic, Google, and other APIs
2. **Advanced Token Tracking**: Real token consumption tracking for actual API usage
3. **Expanded Harness Support**: Support for more agent harnesses and models
4. **Cloud Integration**: Integration with cloud platforms for distributed execution

## Development Guidelines

### Adding New Harnesses

To add a new harness, modify the `harness_pool` in `multi_harness_config.json`:

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

### Performance Optimization

1. **Optimize Harness Selection**: Improve task complexity analysis
2. **Cache Results**: Cache harness selection and execution results
3. **Async Processing**: Use async/await for better performance
4. **Resource Management**: Monitor and optimize resource usage

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

### Getting Help

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
- **Cloud Providers**: Integration with cloud platforms

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.

## Contact

For questions, issues, or contributions, please:

- **GitHub**: https://github.com/multi-harness/orchestrator
- **Email**: contact@example.com

## Support

This project is actively maintained and improved. Please report any issues and contribute to the project!
