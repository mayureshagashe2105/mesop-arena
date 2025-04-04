
# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
""" Generate Images from models in Model Garden or Gemini """

import base64
import io
import logging
import time
from typing import Any
import uuid
import random
import os

from PIL import Image

from google.cloud import aiplatform
from google.cloud.firestore import Client, FieldFilter
import vertexai
from vertexai.preview.vision_models import ImageGenerationModel

from config.default import Default
from config.firebase_config import FirebaseClient
from common.storage import store_to_gcs
from common.metadata import add_image_metadata


config = Default()
logging.basicConfig(level=logging.DEBUG)


def base64_to_image(image_str: str) -> Any:
    """Convert base64 encoded string to an image.

    Args:
      image_str: A string of base64 encoded image.

    Returns:
      A PIL.Image instance.
    """
    image = Image.open(io.BytesIO(base64.b64decode(image_str)))
    return image


def images_from_flux(model_name: str, prompt: str, aspect_ratio: str):
    """
    Creates images from Model Garden deployed Flux.1 model,
    Returns a list of gcs uris
    """
    _ = aspect_ratio  # aspect ratio is not used in this function
    start_time = time.time()

    arena_output = []
    logging.info("model: %s", model_name)
    logging.info("prompt: %s", prompt)
    logging.info("target output: %s", config.GENMEDIA_BUCKET)

    aiplatform.init(project=config.PROJECT_ID, location=config.LOCATION)

    instances = [{"text": prompt}]
    parameters = {
        "height": 1024,
        "width": 1024,
        "num_inference_steps": 4,
    }

    endpoint = aiplatform.Endpoint(
        f"projects/{config.PROJECT_ID}/locations/{config.LOCATION}/endpoints/{config.MODEL_FLUX1_ENDPOINT_ID}"
    )

    print("calling endpoint")
    response = endpoint.predict(
        instances=instances,
        parameters=parameters,
    )

    end_time = time.time()
    elapsed_time = end_time - start_time

    images = [
        # base64_to_image(prediction.get("output")) for prediction in response.predictions
        prediction.get("output")
        for prediction in response.predictions
    ]

    for idx, img in enumerate(images):
        logging.info(
            "Generated image %s with model %s in %s seconds",
            idx,
            model_name,
            f"{elapsed_time:.2f}",
        )

        gcs_uri = store_to_gcs("flux1", f"{uuid.uuid4()}.png", "image/png", img, True)
        gcs_uri = f"gs://{gcs_uri}"  # append "gs://"

        logging.info("generated image: %s len %s at %s", idx, len(img), gcs_uri)
        arena_output.append(gcs_uri)
        logging.info("image created: %s", gcs_uri)
        try:
            add_image_metadata(gcs_uri, prompt, model_name)
        except Exception as e:
            if "DeadlineExceeded" in str(e):  # Check for timeout error
                logging.error("Firestore timeout: %s", e)
            else:
                logging.error("Error adding image metadata: %s", e)
    return arena_output

def images_from_imagen(model_name: str, prompt: str, aspect_ratio: str):
    """creates images from Imagen and returns a list of gcs uris
    Args:
        model_name (str): imagen model name
        prompt (str): prompt for t2i model
        aspect_ratio (str): aspect ratio string
    Returns:
        _type_: a list of strings (gcs uris of image output)
    """

    start_time = time.time()

    arena_output = []
    logging.info(f"model: {model_name}")
    logging.info(f"prompt: {prompt}")
    logging.info(f"target output: {config.GENMEDIA_BUCKET}")

    vertexai.init(project=config.PROJECT_ID, location=config.LOCATION)

    image_model = ImageGenerationModel.from_pretrained(model_name)

    response = image_model.generate_images(
        prompt=prompt,
        add_watermark=True,
        # aspect_ratio=getattr(state, "image_aspect_ratio"),
        aspect_ratio=aspect_ratio,
        number_of_images=1,
        output_gcs_uri=f"gs://{config.GENMEDIA_BUCKET}/imagen_live",
        language="auto",
        # negative_prompt=state.image_negative_prompt_input,
        safety_filter_level="block_few",
        # include_rai_reason=True,
    )
    end_time = time.time()
    elapsed_time = end_time - start_time

    for idx, img in enumerate(response.images):
        logging.info(f"Generated image {idx} with model {model_name} in {elapsed_time:.2f} seconds")

        logging.info(
            f"Generated image: #{idx}, len {len(img._as_base64_string())} at {img._gcs_uri}"
        )
        # output = img._as_base64_string()
        # state.image_output.append(output)
        arena_output.append(img._gcs_uri)
        logging.info(f"Image created: {img._gcs_uri}")
        try:
            add_image_metadata(img._gcs_uri, prompt, model_name)
        except Exception as e:
            if "DeadlineExceeded" in str(e):  # Check for timeout error
                logging.error(f"Firestore timeout: {e}")
            else:
                logging.error(f"Error adding image metadata: {e}")

    return arena_output

def study_fetch(model_name: str, prompt: str) -> list[str]:
    db: Client = FirebaseClient(database_id=config.IMAGE_FIREBASE_DB).get_client()
    collection_ref = db.collection(config.IMAGE_COLLECTION_NAME)
    print(f"Using: {model_name}")

    query = collection_ref.where(filter=FieldFilter("prompt", "==", prompt)).where(filter=FieldFilter("model", "==", model_name)).stream()

    docs = []
    for doc in query:
        gs_uri = doc.to_dict()['gcsuri']
        if "stablediffusion" not in gs_uri:
            docs.append(os.path.splitext(gs_uri)[0])
        else:
            if gs_uri.startswith("20250328_"):
                docs.append(os.path.splitext(gs_uri)[0])
            else:
                docs.append(gs_uri)
    return random.sample(docs, 1)

if __name__ == "__main__":
    # Example usage
    prompt = "A futuristic city skyline at sunset"
    aspect_ratio = "16:9"
    model_name = config.MODEL_FLUX1

    images = images_from_flux(model_name, prompt, aspect_ratio)
    print("Generated images:", images[0])

