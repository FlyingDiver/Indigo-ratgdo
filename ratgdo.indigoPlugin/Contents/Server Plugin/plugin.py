#! /usr/bin/env python
# -*- coding: utf-8 -*-
####################

import logging

RATGDO_MESSAGE_TYPE = "##ratgdo##"

################################################################################
class Plugin(indigo.PluginBase):

    ########################################
    # Main Plugin methods
    ########################################
    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)

        pfmt = logging.Formatter('%(asctime)s.%(msecs)03d\t[%(levelname)8s] %(name)20s.%(funcName)-25s%(msg)s', datefmt='%Y-%m-%d %H:%M:%S')
        self.plugin_file_handler.setFormatter(pfmt)
        self.logLevel = int(self.pluginPrefs.get("logLevel", logging.INFO))
        self.indigo_log_handler.setLevel(self.logLevel)
        self.logger.debug(f"logLevel = {self.logLevel}")

        self.ratgdo_devices = []
        self.mqttPlugin = indigo.server.getPlugin("com.flyingdiver.indigoplugin.mqtt")

        if old_version := self.pluginPrefs.get("version", "0.0.0") != self.pluginVersion:
            self.logger.debug(f"Upgrading plugin from version {old_version} to {self.pluginVersion}")
            self.pluginPrefs["version"] = self.pluginVersion

    def startup(self):
        self.logger.info("Starting ratgdo")

        if not self.mqttPlugin.isEnabled():
            return "MQTT Connector plugin not enabled!"

        indigo.server.subscribeToBroadcast("com.flyingdiver.indigoplugin.mqtt", "com.flyingdiver.indigoplugin.mqtt-message_queued", "message_handler")

    def message_handler(self, notification):
        self.logger.debug(f"message_handler: MQTT message {notification['message_type']} from {indigo.devices[int(notification['brokerID'])].name}")
        self.processMessage(notification)

    def deviceStartComm(self, device):
        self.logger.info(f"{device.name}: Starting Device")
        if device.id not in self.ratgdo_devices:
            self.ratgdo_devices.append(device.id)

    def deviceStopComm(self, device):
        self.logger.info(f"{device.name}: Stopping Device")
        if device.id in self.ratgdo_devices:
            self.ratgdo_devices.remove(device.id)

    def processMessage(self, notification):

        if notification["message_type"] != RATGDO_MESSAGE_TYPE:
            return

        props = {'message_type': RATGDO_MESSAGE_TYPE}
        brokerID = int(notification['brokerID'])
        while True:
            message_data = self.mqttPlugin.executeAction("fetchQueuedMessage", deviceId=brokerID, props=props, waitUntilDone=True)
            if message_data is None:
                break
            self.logger.debug(f"processMessage: {message_data}")

            for device_id in self.ratgdo_devices:
                device = indigo.devices[device_id]
                topic_parts = message_data["topic_parts"]
                payload = message_data["payload"]

                if topic_parts[1] != device.address:     # wrong device
                    continue

                if topic_parts[2] != "status":           # wrong topic
                    continue

                ratgdo_status = topic_parts[3]
                
                # if a newer version of ratgdo sends statuses we don't know
                # about, let's just skip them until we can add them to
                # Devices.xml as known states.
                if ratgdo_status in device.states.keys():
                    device.updateStateOnServer(key=ratgdo_status, value=payload)
                else:
                    self.logger.debug(f"processMessage: status type {ratgdo_status} not a known state, skipping updating on server")

                if ratgdo_status == "door":
                    if payload == "closed":
                        device.updateStateOnServer(key="onOffState", value=True)    # Locked (closed) = True
                    else:
                        device.updateStateOnServer(key="onOffState", value=False)    # Unlocked (open) = False

    @staticmethod
    def get_mqtt_connectors(filter="", valuesDict=None, typeId="", targetId=0):
        retList = []
        devicePlugin = valuesDict.get("devicePlugin", None)
        for dev in indigo.devices.iter():
            if dev.protocol == indigo.kProtocol.Plugin and dev.pluginId == "com.flyingdiver.indigoplugin.mqtt" and dev.deviceTypeId == 'mqttBroker':
                retList.append((dev.id, dev.name))
        retList.sort(key=lambda tup: tup[1])
        return retList

    def create_trigger(self, valuesDict, typeId):

        found = False

        # look to see if there's an existing trigger for this message-type
        for trigger in indigo.triggers:
            try:
                if trigger.pluginId != 'com.flyingdiver.indigoplugin.mqtt':
                    continue
                if trigger.pluginTypeId != 'topicMatch':
                    continue
                self.logger.debug(f"startup: Checking existing trigger: {trigger}")
                if trigger.globalProps['com.flyingdiver.indigoplugin.mqtt']['message_type'] == RATGDO_MESSAGE_TYPE and \
                        trigger.globalProps['com.flyingdiver.indigoplugin.mqtt']['brokerID'] == valuesDict['brokerID']:
                    self.logger.info(f"Skipping trigger creation, trigger already exists: {trigger.name}")
                    found = True
                    return True
            except Exception as e:
                self.logger.debug(f"startup: Error reading trigger: {trigger}\n{e}")
                continue

        if not found:
            broker = indigo.devices[int(valuesDict['brokerID'])]
            name = f"ratgdo Trigger ({broker.name})"
            try:
                indigo.pluginEvent.create(name=name, pluginId="com.flyingdiver.indigoplugin.mqtt", pluginTypeId="topicMatch",
                    props={
                        "brokerID": valuesDict['brokerID'],
                        "message_type": RATGDO_MESSAGE_TYPE,
                        "queueMessage": "True",
                        "match_list": ["Match: ratgdo", "Any: ", "Match: status"]
                    })
            except Exception as e:
                self.logger.error(f"Error calling indigo.pluginEvent.create(): {e}")
            else:
                self.logger.info(f"Created trigger '{name} for message type '{RATGDO_MESSAGE_TYPE}'")

        return True

    ########################################
    # Relay / Dimmer Action callback
    ########################################

    def actionControlDevice(self, action, device):

        if action.deviceAction == indigo.kDeviceAction.Unlock:
            self.logger.debug(f"actionControlDevice: Unlock {device.name}")
            self.publish_topic(device, f"ratgdo/{device.address}/command/door", "open")

        elif action.deviceAction == indigo.kDeviceAction.Lock:
            self.logger.debug(f"actionControlDevice: Lock {device.name}")
            self.publish_topic(device, f"ratgdo/{device.address}/command/door", "close")

        elif action.deviceAction == indigo.kUniversalAction.RequestStatus:
            self.logger.debug(f"{device.name}: actionControlUniversal: RequestStatus")
#            self.publish_topic(device, f"ratgdo/{device.address}/command", "query")
            self.publish_topic(device, f"ratgdo/{device.address}/command/query", "")

        else:
            self.logger.error(f"{device.name}: actionControlDevice: Unsupported action requested: {action.deviceAction}")


    def publish_topic(self, device, topic, payload):

        mqttPlugin = indigo.server.getPlugin("com.flyingdiver.indigoplugin.mqtt")
        if not mqttPlugin.isEnabled():
            self.logger.error("MQTT Connector plugin not enabled, publish_topic aborting.")
            return

        brokerID = int(device.pluginProps['brokerID'])
        props = {
            'topic': topic,
            'payload': payload,
            'qos': 0,
            'retain': 0,
        }
        mqttPlugin.executeAction("publish", deviceId=brokerID, props=props, waitUntilDone=False)
        self.logger.debug(f"{device.name}: publish_topic: {topic} -> {payload}")

    ########################################
    ########################################
    # PluginConfig methods
    ########################################

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        if not userCancelled:
            self.logLevel = int(valuesDict.get("logLevel", logging.INFO))
            self.indigo_log_handler.setLevel(self.logLevel)

    ########################################
    # Custom Plugin Action callbacks (defined in Actions.xml)
    ########################################

    def pickDevice(self, filter=None, valuesDict=None, typeId=0, targetId=0):
        retList = []
        for devID in self.shimDevices:
            device = indigo.devices[int(devID)]
            retList.append((device.id, device.name))
        retList.sort(key=lambda tup: tup[1])
        return retList

