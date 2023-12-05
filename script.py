import os
import time
import random
import time
import requests 
import re
import logging
import subprocess
from subprocess import Popen
from sys import platform
import os, sys
import logging
import json
import threading

from netunicorn.client.remote import RemoteClient, RemoteClientException
from netunicorn.base import Experiment, ExperimentStatus, Pipeline
from netunicorn.library.tasks.basic import SleepTask
from netunicorn.library.tasks.measurements.ping import Ping
from netunicorn.base.architecture import Architecture
from netunicorn.base.nodes import Node
from netunicorn.base.task import Failure, Task, TaskDispatcher
from netunicorn.base import Result, Failure, Success, Task, TaskDispatcher
from netunicorn.base.architecture import Architecture
from netunicorn.base.nodes import Node

# our imports
#watchers
from netunicorn.library.tasks.video_watchers.youtube_watcher import WatchYouTubeVideo
from netunicorn.library.tasks.video_watchers.vimeo_watcher import WatchVimeoVideo
# add vimeo and twitch
#pcaps
from netunicorn.library.tasks.capture.tcpdump import StartCapture, StopNamedCapture
#speedtest
from netunicorn.library.tasks.measurements.ookla_speedtest import SpeedTest

from netunicorn.library.tasks.upload.fileio import UploadToFileIO

from typing import Dict
from typing import Optional
from enum import IntEnum
from datetime import datetime

from typing import Dict
from typing import Optional
from enum import IntEnum
from datetime import datetime

from returns.pipeline import is_successful
from returns.result import Failure


class MkdirTask(TaskDispatcher):
    def __init__(self, filepath: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.linux_implementation = MkdirTaskLinuxImplementation(
            filepath=filepath, name=self.name
        )

    def dispatch(self, node: Node) -> Task:
        if node.architecture in {Architecture.LINUX_AMD64, Architecture.LINUX_ARM64}:
            return self.linux_implementation
        raise NotImplementedError(
            f"MkdirTask is not implemented for architecture: {node.architecture}"
        )

class MkdirTaskLinuxImplementation(Task):
    def __init__(self, filepath: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filepath = filepath

    # TODO: implement the run() function
    def run(self):
        # 1. set the arguments for subprocess.run to exectue the command `mkdir self.dirpath`
        args = ["mkdir", "-p", self.filepath]
        # 2. set the optional argument `capture_output` to True so that stdout will be captured
        result = subprocess.run(args, capture_output=True)
        
        if result.returncode != 0:
            return Failure(
                result.stdout.decode("utf-8").strip()
                + "\n"
                + result.stderr.decode("utf-8").strip()
            )

        return result.stdout.decode("utf-8")


class ScpTask(TaskDispatcher):    
    def __init__(self, src_path: str, dest_path: str, password: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.linux_implementation = ScpTaskLinuxImplementation(
            src_path=src_path, dest_path=dest_path, name=self.name, password=password
        )

    def dispatch(self, node: Node) -> Task:
        if node.architecture in {Architecture.LINUX_AMD64, Architecture.LINUX_ARM64}:
            return self.linux_implementation
        raise NotImplementedError(
            f"ScpTask is not implemented for architecture: {node.architecture}"
        )

class ScpTaskLinuxImplementation(Task):
    requirements = ["sudo apt-get update", "sudo apt-get install -y expect"]

    def __init__(self, src_path: str, dest_path: str, password: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.src_path = src_path
        self.dest_path = dest_path
        self.password = password

    def run(self):

        command = f"scp {self.src_path} {self.dest_path}"

        expect_script = f"""
        set timeout -1
        spawn {command}
        expect "Are you sure you want to continue connecting"
        send "yes\\r"
        expect "password:"
        send "{self.password}\\r"
        expect eof
        """

        try:
            result = subprocess.run(['expect'], input=expect_script, text=True, capture_output=True)

            if result.returncode != 0:
                error_message = (
                    result.stdout.strip()
                    + "\n"
                    + result.stderr.strip()
                    + " SCP TASK FAILED"
                    + "\n"
                    + command
                )
                return Failure(error_message)
            else:
                return Success(result.stdout.strip())
        except Exception as e:
            return Failure(f"Unexpected error: {str(e)}")


NETUNICORN_ENDPOINT = os.environ.get('NETUNICORN_ENDPOINT', 'https://pinot.cs.ucsb.edu/netunicorn')
NETUNICORN_LOGIN = os.environ.get('NETUNICORN_LOGIN', 'team_tz')       
NETUNICORN_PASSWORD = os.environ.get('NETUNICORN_PASSWORD', 'throughput445')

client = RemoteClient(endpoint=NETUNICORN_ENDPOINT, login=NETUNICORN_LOGIN, password=NETUNICORN_PASSWORD)
print("Health Check: {}".format(client.healthcheck()))
nodes = client.get_nodes().filter(lambda node: node.name.startswith("raspi"))[0]
print(nodes)
print(len(nodes))

def execute_pipeline(pipeline, experiment_label, concurrent_nodes):
    client = RemoteClient(endpoint=NETUNICORN_ENDPOINT, login=NETUNICORN_LOGIN, password=NETUNICORN_PASSWORD)
    nodes = client.get_nodes()
    working_nodes = nodes.filter(lambda node: node.name.startswith("raspi")).take(concurrent_nodes)
    experiment = Experiment().map(pipeline, working_nodes)

    print("working_nodes: " + str(working_nodes))

    from netunicorn.base import DockerImage
    for deployment in experiment:
        # you can explore the image on the DockerHub
        deployment.environment_definition = DockerImage(image='speeeday/chromium-speedtest:0.3.1')

    try:
        client.delete_experiment(experiment_label)
    except RemoteClientException:
        pass

    # Prepare Experiment
    client.prepare_experiment(experiment, experiment_label)
    while True:
        info = client.get_experiment_status(experiment_label)
        print(info.status)
        if info.status == ExperimentStatus.READY:
            break
        time.sleep(20)

    time.sleep(5)

    # Execute Experiment
    client.start_execution(experiment_label)
    while True:
        info = client.get_experiment_status(experiment_label)
        print(info.status)
        if info.status != ExperimentStatus.RUNNING:
            break
        time.sleep(20)
    return info


def display_results(info, directory):
    # if directory does not already exist, create it
    if not os.path.exists(directory):
        os.makedirs(directory)

    with open(f"{directory}/results.txt", "w") as f:
        for report in info.execution_result:
            f.write(f"Node name: {report.node.name}\n")
            f.write(f"Error: {report.error}\n")

            if report.result is None:
                f.write("report.result is EMPTY..\n")
                continue

            result, log = report.result  # report stores results of execution and corresponding log

            # result is a returns.result.Result object, could be Success of Failure
            f.write(f"Result is: {type(result)}\n")
            if is_successful(result):
                print("SUCCESS")
                data = result.unwrap()
            else:
                print("FAILURE")
                data = result.failure()
            try:
                for key, value in data.items():
                    f.write(f"{key}: {value}\n")
            except:
                f.write(f"No attribute 'items' in result\n")

            # we also can explore logs
            for line in log:
                f.write(f"{line.strip()}\n")
            f.write("\n")
            
            return (is_successful(result))

start_total_time = time.time()

# loop to execute pipeline for all the nodes
#iterate 30 times
for i in range(901):
    start_time = time.time()

    print("iteration: " + str(i))
    # generate a random number between 1 and 9
    # running_node_count = random.randint(1, 9)
    running_node_count = 1
    print("running_node_count: " + str(running_node_count))
    
    succeeded = False
    
    while (succeeded == False):
        # define the pipeline with unique pcap names
        pipeline = (Pipeline()
                    .then(MkdirTask(filepath='/tmp/ziv_experiment_captures/'+ str(i)))
                    .then(SpeedTest())
                    .then(StartCapture(filepath='/tmp/ziv_experiment_captures/'+ str(i) +'/videocapture.pcap', name="capture"))
                    .then(SleepTask(seconds=2))
                    .then(WatchVimeoVideo(video_url="https://vimeo.com/874415317#t=44s", duration=10))
                    .then(SleepTask(seconds=2))
                    .then(StopNamedCapture(start_capture_task_name="capture"))
                    .then(ScpTask(src_path='/tmp/ziv_experiment_captures/'+ str(i) +'/videocapture.pcap', dest_path="ziv@csil.cs.ucsb.edu:/cs/student/ziv/f23/cs190N/data_collection_pcaps/" + str(i), password="CSIL_PASSWORD_HERE"))
                    .then(SpeedTest())
        )

        try:
            print("executing pipeline!")
            info = execute_pipeline(pipeline, "vimeoWatchExperimentLOOP" + str(i), running_node_count)
            print("pipeline execution done!")

            # redirect the output of display_results(info)...
            succeeded = display_results(info, './ziv_experiment_captures/'+ str(i))

            elapsed_time = time.time() - start_time
            elapsed_minutes = int(elapsed_time / 60)
            elapsed_seconds = int(elapsed_time % 60)
            print(f"Iteration {i} completed in {elapsed_minutes} minutes and {elapsed_seconds} seconds")


             # calculate estimated remaining time
            average_time_per_iteration = (time.time() - start_total_time) / (i + 1)
            remaining_iterations = 901 - (i + 1)
            estimated_remaining_time = average_time_per_iteration * remaining_iterations

            # convert estimated remaining time to days, hours, minutes, and seconds
            remaining_days = int(estimated_remaining_time / (24 * 60 * 60))
            remaining_hours = int((estimated_remaining_time % (24 * 60 * 60)) / (60 * 60))
            remaining_minutes = int((estimated_remaining_time % (60 * 60)) / 60)
            remaining_seconds = int(estimated_remaining_time % 60)

            print(f"Estimated remaining time: {remaining_days} days, {remaining_hours} hours, {remaining_minutes} minutes, {remaining_seconds} seconds")
            
        except Exception as e:
            print(f"Error occurred: {e}")
            print("Retrying...")
            time.sleep(2)
        


