# Author: Nomaan Khan
# Created: 1/3/25
# Last change: 1/3/25 (Nomaan Khan)

# A Python script to perform parallel health checks on HTTP endpoints provided in a YAML file.

# Usage:
# python healthCheck.py -f <path_to_yaml_file> [-t <threads>] [-cy <cycle_length>] [-c <colorize_output>] [-v <verbose>]
#
# Arguments:
# -f, --file          (Required) Path to the YAML file containing the endpoint definitions
# -t, --threads       (Optional) Maximum number of threads for parallel HTTP requests (default: 10)
# -cy, --cycleLength  (Optional) Time in seconds to wait before running the next health check cycle (default: 15 seconds)
# -c, --colorize      (Optional) Enable or disable colorized output (default: True)
# -v, --verbose       (Optional) Enable or disable verbose logging (default: False)
#
# Example:
# python healthCheck.py -f endpoints.yaml -cy 15 -c true -v true -t 5
#
# The above example checks the endpoints defined in `endpoints.yaml` in 15-second cycles,
# outputs colorized results, logs detailed request and response information, and uses 5 threads for parallel requests

##################################################################
# Imports
##################################################################
import argparse                                                  # For parsing command-line arguments
import yaml                                                      # For reading YAML input files
from urllib.parse import urlparse                                # For parsing the urls
import requests                                                  # For making HTTP calls
import time                                                      # For introducing delays between health checks
from collections import defaultdict                              # For default dictionary to store endpoint stats
import sys                                                       # For handling system-level operations like exiting
from concurrent.futures import ThreadPoolExecutor, as_completed  # For parallelizing HTTP calls
import threading                                                 # For locking results to prevent race condtion for results

##################################################################
# Function to parse command-line arguments
##################################################################
def parseArgs():
    # Valid choices for boolean args
    booleanChoices = ['t', 'T', 'true', 'True', 'TRUE', 'f', 'F', 'false', 'False', 'FALSE']

    # Setting up the argument parser
    parser = argparse.ArgumentParser(
        description="Description for healthCheck.py",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    # Adding arguments for the file path, cycle length, colorization, verbosity, and threads
    parser.add_argument("-f", "--file", help="Path to YAML file with endpoints", required=True)
    parser.add_argument("-t", "--threads", help="Maximum number of threads for parallel requests", required=False, type=int, default=10)
    parser.add_argument("-cy", "--cycleLength", help="Time to sleep in seconds before running health check again", required=False, type=float, default=15)
    parser.add_argument("-c", "--colorize", help="BOOLEAN to colorize output", choices=booleanChoices, required=False, default="True")
    parser.add_argument("-v", "--verbose", help="BOOLEAN to set log verbosity", choices=booleanChoices, required=False, default="False")
    
    # Parsing arguments
    argument = parser.parse_args()
    
    trueChoices = ['t', 'T', 'true', 'True', 'TRUE']
    
    # Convert string booleans to actual boolean values
    colorize = True if argument.colorize in trueChoices else False
    verbose = True if argument.verbose in trueChoices else False

    return argument.file, argument.cycleLength, colorize, verbose, argument.threads


##################################################################
# Function to parse endpoints from the provided YAML file
##################################################################
def parseFileEndpoints(fileName):
    # Load endpoints from yaml
    with open(fileName, 'r') as file:
        endpoints = yaml.safe_load(file)
    return endpoints


##################################################################
# Function to display the availability results
##################################################################
def printResults(results, colorize, verbose):
    if verbose:
        print("\nAvailability Report:")

    # Loop through results to calculate and print availability percentages
    for domain, stats in results.items():
        availability = round((stats['success'] / stats['total']) * 100) if stats['total'] > 0 else 0
        # Apply color to output if colorize is enabled
        if colorize:
            print(f"\033[91m{domain} has {availability}% availability percentage\033[00m")
        else:
            print(f"{domain} has {availability}% availability percentage")


##################################################################
# Function to make a single HTTP call
##################################################################
def checkEndPoint(endpoint, results, lock, colorize, verbose):
    # Extract endpoint details
    name = endpoint.get('name', 'Unnamed Request')
    url = endpoint.get('url')
    method = endpoint.get('method', 'GET').upper()
    headers = endpoint.get('headers', {})
    body = endpoint.get('body')

    # Extract domain from URL for grouping results
    domain = urlparse(url).netloc

    # Increment number of calls for current domain if thread has lock
    with lock:
        results[domain]['total'] += 1

    try:
        # Start time for the request
        reqStartTime = time.time()
        # Perform HTTP request
        if body:
            response = requests.request(method, url, headers=headers, data=body)
        else:
            response = requests.request(method, url, headers=headers)
        # Calculate latency
        latency = (time.time() - reqStartTime) * 1000
        roundedLatency = round(latency)

        # Check response status and latency
        if 200 <= response.status_code < 300 and latency < 500:
            # If UP then increment success count for current domain if lock
            with lock:
                results[domain]['success'] += 1
            if verbose:
                if colorize:
                    print(f" - Endpoint with name \033[92m{name}\033[00m has HTTP response code {response.status_code} and latency {roundedLatency} ms => UP")
                else:
                    print(f" - Endpoint with name {name} has HTTP response code {response.status_code} and latency {roundedLatency} ms => UP")
        else:
            reason = "(response code not in range 200–299)" if response.status_code < 200 or response.status_code > 299 else "(latency >= 500 ms)"
            if verbose:
                if colorize:
                    print(f" - Endpoint with name \033[92m{name}\033[00m has HTTP response code {response.status_code} and latency {roundedLatency} ms => DOWN {reason}")
                else:
                    print(f" - Endpoint with name {name} has HTTP response code {response.status_code} and latency {roundedLatency} ms => DOWN {reason}")
    except requests.RequestException as e:
        if verbose:
            print(f" - Endpoint with name {name} encountered an error => DOWN ({e})")


##################################################################
# Function to perform health checks in parallel
##################################################################
def runHealthChecks(endpoints, colorize, verbose, cycleLength, maxThreads):
    # Dictionary to store success and total requests per domain
    results = defaultdict(lambda: {'success': 0, 'total': 0})

    # Counter for cycles
    cycleNumber = 0

    # Store first cycle start time so that you can calculate the 
    # relative cycle start times for next cycles
    firstCycleStartTime = time.time()

    # Lock to prevent race conditions for results dictionary
    lock = threading.Lock()

    # Create a ThreadPoolExecutor with a maximum number of threads
    with ThreadPoolExecutor(max_workers=maxThreads) as executor:
        while True:
            try:
                # Incrent cycle and start it's time
                cycleNumber += 1
                cycleStartTime = time.time()
                if verbose:
                    logCycleStartTime = int(cycleStartTime - firstCycleStartTime)
                    print(f"\nTest cycle #{cycleNumber} begins at time = {logCycleStartTime} seconds:")

                # Submit all endpoint checks to the executor
                futureToEndpoint = {
                    executor.submit(checkEndPoint, endpoint, results, lock, colorize, verbose): endpoint
                    for endpoint in endpoints
                }

                # Wait for all futures to complete
                for future in as_completed(futureToEndpoint):
                    try:
                        future.result()
                    except Exception as e:
                        if verbose:
                            print(f'Exception: {e}')

                # Calculate time left in the current health check cycle and sleep for that time
                currentCycleFinishTime = time.time() - cycleStartTime
                sleepTime = max(0, cycleLength - currentCycleFinishTime)
                
                # Print results of health check
                printResults(results, colorize, verbose)
                
                if verbose:
                    print(f"\nWaiting for {sleepTime:.2f} seconds to complete {cycleLength}s cycle before the next iteration...")

                # Sleep for the specified cycle length
                time.sleep(sleepTime)

            except KeyboardInterrupt:
                # Exit program when user presses ctrl + c
                break


##################################################################
# Entry point for the script
##################################################################
if __name__ == '__main__':
    try:
        # Parse command-line arguments
        file, cycleLength, colorize, verbose, maxThreads = parseArgs()

        # Load endpoint data from YAML file
        endpoints = parseFileEndpoints(file)

        # Perform health checks on the endpoints and print results
        runHealthChecks(endpoints, colorize, verbose, cycleLength, maxThreads)

    except KeyboardInterrupt:
        # Exit program when user presses ctrl + c
        sys.exit(0)
