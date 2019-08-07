import requests
import backoff
import singer

LOGGER = singer.get_logger()

AUTH_URL = "https://pi.pardot.com/api/login/version/3"
ENDPOINT_BASE = "https://pi.pardot.com/api/"

class PardotException(Exception):
    def __init__(self, message, response_content):
        self.code = response_content.get("@attributes", {}).get("err_code")
        self.response = response_content
        super().__init__(message)

class Client():
    """
    Lightweight Client wrapper to allow switching between version 3 and 4
    API based on availability, if desired.
    """
    api_version = None
    api_key = None
    creds = None
    TEST_URL = None
    # TODO: This could probably be refactored
    # {objectName}/version/{api_version}/do/{action}
    endpoint_map = {
        "email_click": "emailClick/version/{}/do/query",
        "prospect_account": "prospectAccount/version/{}/do/query",
        "visitor_activity": "visitorActivity/version/{}/do/query",
    }

    describe_map = {
        "prospect_account": "prospectAccount/version/{}/do/describe",
    }

    def __init__(self, creds):
        # Do login
        self.creds = creds
        self.login()

    def login(self):
        response = requests.post(AUTH_URL,
                                 data={
                                     "email": self.creds["email"],
                                     "password": self.creds["password"],
                                     "user_key": self.creds["user_key"]
                                 },
                                 params={"format":"json"})

        # This will only work if they use HTTP codes. Handling Pardot
        # errors below.
        response.raise_for_status()

        content = response.json()
        
        error_message = content.get("err")
        if error_message:
            error_code = content["@attributes"]["err_code"] # E.g., "15" for login failed
            raise PardotException("Pardot returned error code {} while authenticating. Message: {}".format(error_code, error_message), content)

        self.api_version = content['version']
        self.api_key = content['api_key']

    def _get_auth_header(self):
        return {"Authorization": "Pardot api_key={}, user_key={}".format(self.api_key, self.creds["user_key"])}

    def _make_request(self, url, headers=None, params=None):
        response = requests.get(url, headers=headers, params=params)
        content = response.json()
        error_message = content.get("err")

        if error_message:
            error_code = content["@attributes"]["err_code"] # Error code of 1 is an expired api_key or user_key

            if error_code == "1":
                LOGGER.info("API key or user key expired -- Reauthenticating once")
                self.login()
                response = requests.get(url, headers=headers, params=params)
                content = response.json()

        return content
    
    def describe(self, endpoint, **kwargs):

        describe_url = self.describe_map.get(endpoint)

        if describe_url is None:
            raise Exception("No describe operation for endpoint {}".format(endpoint))

        url = (ENDPOINT_BASE + describe_url).format(self.api_version)

        headers = self._get_auth_header()
        params={"format":"json", "output": "bulk", **kwargs}
        
        LOGGER.info("%s - Making request to GET endpoint %s, with params %s", endpoint, url, params)
        content = self._make_request(url, headers, params)
        
        error_message = content.get("err")
        if error_message:
            error_code = content["@attributes"]["err_code"] # E.g., "15" for login failed
            # TODO: This should use a custom exception type so that the calling code can check for retryable errors
            # - And the client itself can wrap and check for relogin being needed
            # - Error code 1 is invalid API key or user key - aka relogin
            raise PardotException("{} - Pardot returned error code {} while describing endpoint. Message: {}".format(endpoint, error_code, error_message), content)

        return content

    def get(self, endpoint, format_params=None, **kwargs):
        # Not worrying about a backoff pattern for the spike
        # Error code 1 indicates a bad api_key or user_key
        # If we get error code 1 then re-authenticate login
        # http://developer.pardot.com/kb/error-codes-messages/#error-code-1
        url = ENDPOINT_BASE + self.endpoint_map[endpoint]
        base_formatting = [self.api_version]
        if format_params:
            base_formatting.extend(format_params)
        url = url.format(*base_formatting)
        # TODO: Switch on version between the quirks of each? Out of
        # scope, not sure if this should be in the client or in the stream
        # implementation

        headers = self._get_auth_header()
        params={"format":"json", "output": "bulk", **kwargs}

        LOGGER.info("%s - Making request to GET endpoint %s, with params %s", endpoint, url, params)
        content = self._make_request(url, headers, params)
        
        error_message = content.get("err")
        if error_message:
            error_code = content["@attributes"]["err_code"] # E.g., "15" for login failed
            # TODO: This should use a custom exception type so that the calling code can check for retryable errors
            # - And the client itself can wrap and check for relogin being needed
            raise PardotException("{} - Pardot returned error code {} while retreiving endpoint. Message: {}".format(endpoint, error_code, error_message), content)

        return content
