from setuptools import setup, find_packages
import os

# Read the contents of README file
with open('README.md', encoding='utf-8') as f:
    long_description = f.read()

setup(
    name="multi-harness-orchestrator",
    version="1.0.0",
    author="Multi-Harness Orchestrator Team",
    author_email="contact@example.com",
    description="A local tool for seamless agent harness coordination with token management and context persistence",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/multi-harness/orchestrator",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Development Status :: 4 - Beta",
    ],
    python_requires='>=3.6',
    install_requires=[
        'adalflow>=1.0.0',
        'python-dateutil>=2.8.0',
        'pydantic>=2.0.0',
    ],
    extras_require={
        'enhanced': [
            'redis>=4.0.0',
            'psutil>=5.9.0',
        ],
    },
    entry_points={
        'console_scripts': [
            'multi-harness-orchestrator=multi_harness_orchestrator:main',
        ],
    },
    include_package_data=True,
    package_data={
        '': ['*.json', '*.md', '*.txt'],
    },
)