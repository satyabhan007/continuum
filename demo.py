#!/usr/bin/env python3
"""
Demonstration script for Multi-Harness Orchestrator

This script demonstrates the key features and usage patterns of the
Multi-Harness Orchestrator for managing multiple agent harnesses.
"""

import asyncio
from multi_harness_orchestrator import MultiHarnessOrchestrator

async def demo_basic_usage():
    """Demonstrate basic orchestrator usage"""
    print("=== Multi-Harness Orchestrator Demo (Simplified) ===\n")
    
    # Initialize orchestrator
    print("1. Initializing orchestrator...")
    orchestrator = MultiHarnessOrchestrator()
    print("   ✓ Orchestrator initialized successfully\n")
    
    # Show harness status
    print("2. Current harness status:")
    for harness_name, harness_info in orchestrator.harnesses.items():
        budget = harness_info['token_budget']
        print(f"   - {harness_name}: {harness_info['status'].value} "
              f"({budget.remaining_tokens} tokens remaining)")
    print()
    
    # Demonstrate task execution
    print("3. Executing sample tasks...")
    
    sample_tasks = [
        "Write a Python script to analyze stock market data",
        "Create a simple web application with Flask",
        "Generate a report on machine learning trends",
        "Translate this text from English to Spanish",
        "Debug a complex Python program"
    ]
    
    for i, task in enumerate(sample_tasks, 1):
        print(f"\n   Task {i}: {task[:50]}...")
        
        # Execute task with orchestrator
        result = await orchestrator.execute_task(task)
        
        if result['success']:
            print(f"   ✓ Executed successfully")
            print(f"     Execution ID: {result['execution_id']}")
            print(f"     Harness used: {result['harness_used']}")
            print(f"     Tokens consumed: {result['tokens_consumed']}")
        else:
            print(f"   ✗ Execution failed: {result['error']}")
            print(f"   Can recover: {result['can_recover']}")
    
    print("\n=== Demo Complete ===")

async def demo_harness_selection():
    """Demonstrate intelligent harness selection"""
    print("\n=== Harness Selection Demo ===\n")
    
    orchestrator = MultiHarnessOrchestrator()
    
    # Tasks with different complexity levels
    tasks = [
        ("Simple text generation", "short"),
        ("Complex code analysis", "complex"),
        ("Data processing", "medium"),
        ("Machine learning model training", "complex"),
        ("Web scraping", "medium")
    ]
    
    print("Task complexity analysis and harness selection:")
    print("-" * 60)
    
    for task, complexity in tasks:
        # Select harness based on task complexity
        selected_harness = orchestrator.select_harness(task)
        
        print(f"Task: {task[:40]:40} | Selected: {selected_harness:15} | Complexity: {complexity}")
    
    print("\n=== Harness Selection Demo Complete ===")

async def demo_token_management():
    """Demonstrate token management and monitoring"""
    print("\n=== Token Management Demo ===\n")
    
    orchestrator = MultiHarnessOrchestrator()
    
    print("Initial token budgets:")
    for harness_name, harness_info in orchestrator.harnesses.items():
        budget = harness_info['token_budget']
        print(f"   - {harness_name}: {budget.total_budget} total tokens")
    
    print("\nSimulating task execution and token consumption...")
    
    # Simulate token consumption
    sample_tasks = [
        "Analyze data",
        "Generate report",
        "Process text",
        "Create visualization"
    ]
    
    for task in sample_tasks:
        result = await orchestrator.execute_task(task)
        
        if result['success']:
            print(f"   Executed: {task:25} | Tokens used: {result['tokens_consumed']:6} | "
                  f"Harness: {result['harness_used']}")
        else:
            print(f"   Failed: {task:25} | Error: {result['error']}")
    
    print("\nFinal token budgets:")
    for harness_name, harness_info in orchestrator.harnesses.items():
        budget = harness_info['token_budget']
        print(f"   - {harness_name}: {budget.used_tokens}/{budget.total_budget} "
              f"({budget.remaining_tokens} remaining)")
    
    print("\n=== Token Management Demo Complete ===")

async def demo_handoff_simulation():
    """Demonstrate handoff simulation"""
    print("\n=== Handoff Simulation Demo ===\n")
    
    orchestrator = MultiHarnessOrchestrator()
    
    print("Simulating token exhaustion and handoff...")
    print("(In this demo, we'll simulate handoff by manually triggering it)")
    
    # Execute a task
    task = "Analyze complex dataset"
    print(f"\nExecuting task: {task}")
    
    result = await orchestrator.execute_task(task)
    
    if result['success']:
        print(f"✓ Task completed successfully")
        print(f"  Execution ID: {result['execution_id']}")
        print(f"  Harness used: {result['harness_used']}")
        print(f"  Tokens consumed: {result['tokens_consumed']}")
        
        # In a real scenario, handoff would be triggered automatically
        # when token limits are reached. Here we simulate it.
        print(f"\nSimulating handoff due to token constraints...")
        print(f"  Current harness: {result['harness_used']}")
        print(f"  Would handoff to: {orchestrator.find_next_available_harness(result['harness_used'])}")
    
    print("\n=== Handoff Simulation Demo Complete ===")

async def main():
    """Main demo function"""
    print("Multi-Harness Orchestrator - Demonstration (Simplified Version)\n")
    print("This demo showcases the key features of the Multi-Harness Orchestrator:")
    print("1. Basic task execution with automatic harness coordination")
    print("2. Intelligent harness selection based on task complexity")
    print("3. Token management and consumption tracking")
    print("4. Handoff simulation when token limits are reached")
    print("\n" + "="*60)
    
    try:
        await demo_basic_usage()
        await demo_harness_selection()
        await demo_token_management()
        await demo_handoff_simulation()
        
        print("\n" + "="*60)
        print("All demonstrations completed successfully!")
        print("\nThe Multi-Harness Orchestrator provides a robust solution for")
        print("managing multiple agent harnesses with seamless coordination,")
        print("token management, and context preservation.")
        
    except Exception as e:
        print(f"\nError during demonstration: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
