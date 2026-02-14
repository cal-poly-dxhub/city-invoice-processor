#!/bin/bash

# Build frontend for CDK deployment (Vite SPA)

set -e

echo "Building frontend for CDK deployment..."

# Navigate to frontend directory
cd frontend

# Check if package.json exists
if [ ! -f "package.json" ]; then
    echo "No package.json found. Checking for existing build..."
    if [ -d "dist" ]; then
        echo "Using existing dist directory"
        exit 0
    else
        echo "Error: No package.json and no dist directory found"
        exit 1
    fi
fi

# Install dependencies if needed
if [ ! -d "node_modules" ]; then
    echo "Installing frontend dependencies..."
    npm install
fi

# Build the Vite app
echo "Building Vite app..."
npm run build

echo "Frontend build completed successfully!"
echo "Build output is in: frontend/dist"
