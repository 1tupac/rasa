# -*- coding: utf-8 -*-

import datetime
import json
import logging
import requests
from sanic import Blueprint, response
from sanic.request import Request
from typing import Text, Dict, Any, List, Iterable

from rasa.core.channels.channel import UserMessage, OutputChannel, InputChannel

logger = logging.getLogger(__name__)

MICROSOFT_OAUTH2_URL = "https://login.microsoftonline.com"

MICROSOFT_OAUTH2_PATH = "botframework.com/oauth2/v2.0/token"


class BotFramework(OutputChannel):
    """A Microsoft Bot Framework communication channel."""

    token_expiration_date = datetime.datetime.now()

    headers = None

    @classmethod
    def name(cls):
        return "botframework"

    def __init__(
        self,
        app_id: Text,
        app_password: Text,
        conversation: Dict[Text, Any],
        bot: Text,
        service_url: Text,
    ) -> None:

        self.app_id = app_id
        self.app_password = app_password
        self.conversation = conversation
        self.global_uri = "{}v3/".format(service_url)
        self.bot = bot

    async def _get_headers(self):
        if BotFramework.token_expiration_date < datetime.datetime.now():
            uri = "{}/{}".format(MICROSOFT_OAUTH2_URL, MICROSOFT_OAUTH2_PATH)
            grant_type = "client_credentials"
            scope = "https://api.botframework.com/.default"
            payload = {
                "client_id": self.app_id,
                "client_secret": self.app_password,
                "grant_type": grant_type,
                "scope": scope,
            }

            token_response = requests.post(uri, data=payload)

            if token_response.ok:
                token_data = token_response.json()
                access_token = token_data["access_token"]
                token_expiration = token_data["expires_in"]

                delta = datetime.timedelta(seconds=int(token_expiration))
                BotFramework.token_expiration_date = datetime.datetime.now() + delta

                BotFramework.headers = {
                    "content-type": "application/json",
                    "Authorization": "Bearer %s" % access_token,
                }
                return BotFramework.headers
            else:
                logger.error("Could not get BotFramework token")
        else:
            return BotFramework.headers

    def prepare_message(
        self, recipient_id: Text, message_data: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        data = {
            "type": "message",
            "recipient": {"id": recipient_id},
            "from": self.bot,
            "channelData": {"notification": {"alert": "true"}},
            "text": "",
        }
        data.update(message_data)
        return data

    async def send(self, message_data: Dict[Text, Any]) -> None:
        post_message_uri = "{}conversations/{}/activities".format(
            self.global_uri, self.conversation["id"]
        )
        headers = await self._get_headers()
        send_response = requests.post(
            post_message_uri, headers=headers, data=json.dumps(message_data)
        )

        if not send_response.ok:
            logger.error(
                "Error trying to send botframework messge. Response: %s",
                send_response.text,
            )

    async def send_text_message(
        self, recipient_id: Text, text: Text, **kwargs: Any
    ) -> None:
        for message_part in text.split("\n\n"):
            text_message = {"text": message_part}
            message = self.prepare_message(recipient_id, text_message)
            await self.send(message)

    async def send_image_url(
        self, recipient_id: Text, image: Text, **kwargs: Any
    ) -> None:
        hero_content = {
            "contentType": "application/vnd.microsoft.card.hero",
            "content": {"images": [{"url": image}]},
        }

        image_message = {"attachments": [hero_content]}
        message = self.prepare_message(recipient_id, image_message)
        await self.send(message)

    async def send_text_with_buttons(
        self,
        recipient_id: Text,
        text: Text,
        buttons: List[Dict[Text, Any]],
        **kwargs: Any
    ) -> None:
        hero_content = {
            "contentType": "application/vnd.microsoft.card.hero",
            "content": {"subtitle": text, "buttons": buttons},
        }

        buttons_message = {"attachments": [hero_content]}
        message = self.prepare_message(recipient_id, buttons_message)
        await self.send(message)

    async def send_elements(
        self, recipient_id: Text, elements: Iterable[Dict[Text, Any]], **kwargs: Any
    ) -> None:
        for e in elements:
            message = self.prepare_message(recipient_id, e)
            await self.send(message)

    async def send_custom_json(
        self, recipient_id: Text, json_message: Dict[Text, Any], **kwargs: Any
    ) -> None:
        # pytype: disable=attribute-error
        json_message.setdefault("type", "message")
        json_message.setdefault("recipient", {}).setdefault("id", recipient_id)
        json_message.setdefault("from", self.bot)
        json_message.setdefault("channelData", {}).setdefault(
            "notification", {}
        ).setdefault("alert", "true")
        json_message.setdefault("text", "")
        await self.send(json_message)
        # pytype: enable=attribute-error


class BotFrameworkInput(InputChannel):
    """Bot Framework input channel implementation."""

    @classmethod
    def name(cls):
        return "botframework"

    @classmethod
    def from_credentials(cls, credentials):
        if not credentials:
            cls.raise_missing_credentials_exception()

        return cls(credentials.get("app_id"), credentials.get("app_password"))

    def __init__(self, app_id: Text, app_password: Text) -> None:
        """Create a Bot Framework input channel.

        Args:
            app_id: Bot Framework's API id
            app_password: Bot Framework application secret
        """

        self.app_id = app_id
        self.app_password = app_password

    def blueprint(self, on_new_message):

        botframework_webhook = Blueprint("botframework_webhook", __name__)

        # noinspection PyUnusedLocal
        @botframework_webhook.route("/", methods=["GET"])
        async def health(request: Request):
            return response.json({"status": "ok"})

        @botframework_webhook.route("/webhook", methods=["POST"])
        async def webhook(request: Request):
            postdata = request.json

            try:
                if postdata["type"] == "message":
                    out_channel = BotFramework(
                        self.app_id,
                        self.app_password,
                        postdata["conversation"],
                        postdata["recipient"],
                        postdata["serviceUrl"],
                    )

                    logger.debug(json.dumps(postdata, indent=4, sort_keys=True))
                    if postdata.get('attachments'):
                        user_msg = UserMessage(
                            text=(postdata['text'] if postdata.get('text') 
                                  else ""),
                            metadata={"attachments": postdata['attachments']},
                            output_channel=out_channel,
                            sender_id=postdata["from"]["id"],
                            input_channel=self.name(),
                        )
                    elif postdata.get('text'):
                        user_msg = UserMessage(
                            postdata["text"],
                            out_channel,
                            postdata["from"]["id"],
                            input_channel=self.name(),
                        )
                    else:
                        user_msg = UserMessage(
                            json.dumps(postdata["value"]),
                            out_channel,
                            postdata["from"]["id"],
                            input_channel=self.name(),
                        )
                    await on_new_message(user_msg)
                else:
                    logger.info("Not received message type")
            except Exception as e:
                logger.error("Exception when trying to handle message.{0}".format(e))
                logger.debug(e, exc_info=True)
                pass

            return response.text("success")

        return botframework_webhook
