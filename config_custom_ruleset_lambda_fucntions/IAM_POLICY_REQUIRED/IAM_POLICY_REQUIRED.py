
"""
 Rule Name:
   IAM_POLICY_REQUIRED
 Description:
   To check IAM users and roles have a given policy attached directly or through a group.
 Trigger:
   Configuration Change on AWS::IAM::User/AWS::IAM::Role
 Reports on:
   AWS::IAM::User,AWS::IAM::Role
 Rule Parameters:
   | ---------------------- | --------- | -------------------------------------------------------- |
   | Parameter Name         | Type      | Description                                              |
   | ---------------------- | --------- | -------------------------------------------------------- |
   | policyArns             | Required  | Comma separated list of policy ARNs which should be      |
   |                        |           | attached to users and roles.                             |
   |                        |           | Example: "arn:aws:iam::012345678912:policy/MyPolicy"     |
   | ---------------------- | --------- | -------------------------------------------------------- |
   | exceptionList          | Optional  | Represents the IAM users and roles which are exempted    |
   |                        |           | from the IAM Config rule. The valid entities in this list|
   |                        |           | will be compliant by default.                            |
   |                        |           | Example: users:[userName1,userName2],roles:[roleName]    |
   | ---------------------- | --------- | -------------------------------------------------------- |
 Feature:
   As: a Security Officer
   I want: To ensure that IAM roles and users have mandatory policies attached
   In order to: enforce mandatory permissions for IAM entities
 Scenarios:
  Scenario: 1
     Given: No IAM Users or Roles exist
      When: Evaluation occurs
      Then: Return "NOT_APPLICABLE"
   Scenario: 2
     Given: exceptionList is configured
       And: An <entity> listed in the exceptionList is not an alphanumerical string
      When: Evaluation occurs
      Then: Return an error
  Examples:
      |  entity  |
      | IAM User |
      | IAM Role |
   Scenario: 3
     Given: policyArns is configured
       And: policyArns does not contain valid ARNs
      Then: Return an error
   Scenario: 4
     Given: An <entity> exists
       And: exceptionList is configured and valid
       And: The <entity> is listed in the exceptionList
      When: Evaluation occurs
      Then: Return COMPLIANT
  Examples:
      |  entity  |
      | IAM User |
      | IAM Role |
   Scenario: 5
     Given: An <entity> exists
       And: The <entity> has all the policies listed in policyArns attached
      When: Evaluation occurs
      Then: Return COMPLIANT
  Examples:
      |  entity  |
      | IAM User |
      | IAM Role |
   Scenario: 6
     Given: An IAM user exists
       And: The IAM user groups combined have the policies listed in policyArns attached
      When: Evaluation occurs
      Then: Return COMPLIANT
   Scenario: 7
     Given: An IAM user exists
       And: The IAM user does not have all the policies listed in policyArns attached
       And: The IAM user's groups combined do not have all the policy listed in policyArns attached
      When: Evaluation occurs
      Then: return NON_COMPLIANT
   Scenario: 8
     Given: An IAM role exists
       And: The IAM role does not have all the policies listed in policyArns attached
      When: Evaluation occurs
      Then: return NON_COMPLIANT
"""

import json
import re
import datetime
import boto3
import botocore

##############
# Parameters #
##############

# Define the default resource to report to Config Rules
DEFAULT_RESOURCE_TYPE = 'AWS::IAM::Role'

# Set to True to get the lambda to assume the Role attached on the Config Service (useful for cross-account).
ASSUME_ROLE_MODE = False

#############
# Main Code #
#############


def should_ignore_config_item(config_item, ignored_roles, ignored_users):
    return (config_item['resourceType'] == 'AWS::IAM::Role' and config_item['resourceName'] in ignored_roles) \
            or (config_item['resourceType'] == 'AWS::IAM::User' and config_item['resourceName'] in ignored_users) \
            or (config_item['ARN'].rsplit("/")[1] == 'aws-service-role')


def get_attached_policies(configuration_item):
    # Get the users managed policies
    attach_policies = [
        policy["policyArn"] for policy in configuration_item["configuration"].get("attachedManagedPolicies")
    ]

    return attach_policies


def list_contains_all(source_list, items):
    return all(policy in source_list for policy in items)


def paginate(client, method, **kwargs):
    paginator = client.get_paginator(method.__name__)
    for page in paginator.paginate(**kwargs).result_key_iters():
        for result in page:
            yield result


def has_policy_attached(event, configuration_item, policy_arns):
    resource_type = configuration_item['resourceType']
    if resource_type == 'AWS::IAM::User':
        managed_policies = get_attached_policies(configuration_item)
        if list_contains_all(managed_policies, policy_arns):
            return True
        # Additively check the users groups to see if they have the required policies
        client = get_client('iam', event)
        groups = configuration_item["configuration"].get("groupList", [])
        for group in groups:
            attached_policies = paginate(client, client.list_attached_group_policies, **{'GroupName': group})
            for policy in attached_policies:
                managed_policies.append(policy['PolicyArn'])
                if list_contains_all(managed_policies, policy_arns):
                    return True

        return False
    elif resource_type == 'AWS::IAM::Role':
        return list_contains_all(get_attached_policies(configuration_item), policy_arns)

    raise ValueError('Unable to handle resource type {}'.format(resource_type))


def evaluate_compliance(event, configuration_item, valid_rule_parameters):
    """Form the evaluation(s) to be return to Config Rules

    Return either:
    None -- when no result needs to be displayed
    a string -- either COMPLIANT, NON_COMPLIANT or NOT_APPLICABLE
    a dictionary -- the evaluation dictionary, usually built by build_evaluation_from_config_item()
    a list of dictionary -- a list of evaluation dictionary , usually built by build_evaluation()

    Keyword arguments:
    event -- the event variable given in the lambda handler
    configuration_item -- the configurationItem dictionary in the invokingEvent
    valid_rule_parameters -- the output of the evaluate_parameters() representing validated parameters of the Config Rule

    Advanced Notes:
    1 -- if a resource is deleted and generate a configuration change with ResourceDeleted status, the Boilerplate code will put a NOT_APPLICABLE on this resource automatically.
    2 -- if a None or a list of dictionary is returned, the old evaluation(s) which are not returned in the new evaluation list are returned as NOT_APPLICABLE by the Boilerplate code
    3 -- if None or an empty string, list or dict is returned, the Boilerplate code will put a "shadow" evaluation to feedback that the evaluation took place properly
    """
    policy_arns = valid_rule_parameters['policyArns']
    exception_list = valid_rule_parameters["exceptionList"]
    ignored_roles = exception_list["roles"]
    ignored_users = exception_list["users"]

    if should_ignore_config_item(configuration_item, ignored_roles, ignored_users):
        return build_evaluation_from_config_item(configuration_item, 'COMPLIANT', 'Ignored IAM entity')
    elif has_policy_attached(event, configuration_item, policy_arns):
        return build_evaluation_from_config_item(configuration_item, 'COMPLIANT', 'All expected policies attached')

    return build_evaluation_from_config_item(configuration_item, 'NON_COMPLIANT', 'IAM entity missing policies')


def is_valid_arn(arn):
    pattern = re.compile("arn:(aws[a-zA-Z-]*)?:iam::(aws|\d{12}):policy\/[a-zA-Z0-9-_\/]+")
    return pattern.match(arn)


def extract_entities_from_exception_list(entity_type, exception_list):
    pattern = re.compile("{Type}:\s?\[([a-zA-Z0-9-_,]+)\]".format(Type=entity_type))
    matches = pattern.search(exception_list)
    if matches:
        return matches.group(1).replace(' ', '').split(",")
    return []


def evaluate_parameters(rule_parameters):
    """Evaluate the rule parameters dictionary validity. Raise a ValueError for invalid parameters.

    Return:
    anything suitable for the evaluate_compliance()

    Keyword arguments:
    rule_parameters -- the Key/Value dictionary of the Config Rules parameters
    """
    policy_arns = rule_parameters.get("policyArns", "").split(",")
    if not all(is_valid_arn(arn) for arn in policy_arns):
        raise ValueError('Invalid policy ARNs specified in policyArns')

    exception_list = rule_parameters.get("exceptionList", "")

    return {
        'policyArns': policy_arns,
        'exceptionList': {
            'users': extract_entities_from_exception_list('users', exception_list),
            'roles': extract_entities_from_exception_list('roles', exception_list),
        }
    }

####################
# Helper Functions #
####################


# Build an error to be displayed in the logs when the parameter is invalid.
def build_parameters_value_error_response(ex):
    """Return an error dictionary when the evaluate_parameters() raises a ValueError.

    Keyword arguments:
    ex -- Exception text
    """
    return  build_error_response(internalErrorMessage="Parameter value is invalid",
                                 internalErrorDetails="An ValueError was raised during the validation of the Parameter value",
                                 customerErrorCode="InvalidParameterValueException",
                                 customerErrorMessage=str(ex))


# This gets the client after assuming the Config service role
# either in the same AWS account or cross-account.
def get_client(service, event):
    """Return the service boto client. It should be used instead of directly calling the client.

    Keyword arguments:
    service -- the service name used for calling the boto.client()
    event -- the event variable given in the lambda handler
    """
    if not ASSUME_ROLE_MODE:
        return boto3.client(service)
    credentials = get_assume_role_credentials(event["executionRoleArn"])
    return boto3.client(service, aws_access_key_id=credentials['AccessKeyId'],
                        aws_secret_access_key=credentials['SecretAccessKey'],
                        aws_session_token=credentials['SessionToken']
                       )


# This generate an evaluation for config
def build_evaluation(resource_id, compliance_type, event, resource_type=DEFAULT_RESOURCE_TYPE, annotation=None):
    """Form an evaluation as a dictionary. Usually suited to report on scheduled rules.

    Keyword arguments:
    resource_id -- the unique id of the resource to report
    compliance_type -- either COMPLIANT, NON_COMPLIANT or NOT_APPLICABLE
    event -- the event variable given in the lambda handler
    resource_type -- the CloudFormation resource type (or AWS::::Account) to report on the rule (default DEFAULT_RESOURCE_TYPE)
    annotation -- an annotation to be added to the evaluation (default None)
    """
    eval_cc = {}
    if annotation:
        eval_cc['Annotation'] = annotation
    eval_cc['ComplianceResourceType'] = resource_type
    eval_cc['ComplianceResourceId'] = resource_id
    eval_cc['ComplianceType'] = compliance_type
    eval_cc['OrderingTimestamp'] = str(json.loads(event['invokingEvent'])['notificationCreationTime'])
    return eval_cc

def build_evaluation_from_config_item(configuration_item, compliance_type, annotation=None):
    """Form an evaluation as a dictionary. Usually suited to report on configuration change rules.

    Keyword arguments:
    configuration_item -- the configurationItem dictionary in the invokingEvent
    compliance_type -- either COMPLIANT, NON_COMPLIANT or NOT_APPLICABLE
    annotation -- an annotation to be added to the evaluation (default None)
    """
    eval_ci = {}
    if annotation:
        eval_ci['Annotation'] = annotation
    eval_ci['ComplianceResourceType'] = configuration_item['resourceType']
    eval_ci['ComplianceResourceId'] = configuration_item['resourceId']
    eval_ci['ComplianceType'] = compliance_type
    eval_ci['OrderingTimestamp'] = configuration_item['configurationItemCaptureTime']
    return eval_ci

####################
# Boilerplate Code #
####################

# Helper function used to validate input
def check_defined(reference, reference_name):
    if not reference:
        raise Exception('Error: ', reference_name, 'is not defined')
    return reference

# Check whether the message is OversizedConfigurationItemChangeNotification or not
def is_oversized_changed_notification(message_type):
    check_defined(message_type, 'messageType')
    return message_type == 'OversizedConfigurationItemChangeNotification'

# Check whether the message is a ScheduledNotification or not.
def is_scheduled_notification(message_type):
    check_defined(message_type, 'messageType')
    return message_type == 'ScheduledNotification'

# Get configurationItem using getResourceConfigHistory API
# in case of OversizedConfigurationItemChangeNotification
def get_configuration(resource_type, resource_id, configuration_capture_time):
    result = AWS_CONFIG_CLIENT.get_resource_config_history(
        resourceType=resource_type,
        resourceId=resource_id,
        laterTime=configuration_capture_time,
        limit=1)
    configurationItem = result['configurationItems'][0]
    return convert_api_configuration(configurationItem)

# Convert from the API model to the original invocation model
def convert_api_configuration(configurationItem):
    for k, v in configurationItem.items():
        if isinstance(v, datetime.datetime):
            configurationItem[k] = str(v)
    configurationItem['awsAccountId'] = configurationItem['accountId']
    configurationItem['ARN'] = configurationItem['arn']
    configurationItem['configurationStateMd5Hash'] = configurationItem['configurationItemMD5Hash']
    configurationItem['configurationItemVersion'] = configurationItem['version']
    configurationItem['configuration'] = json.loads(configurationItem['configuration'])
    if 'relationships' in configurationItem:
        for i in range(len(configurationItem['relationships'])):
            configurationItem['relationships'][i]['name'] = configurationItem['relationships'][i]['relationshipName']
    return configurationItem

# Based on the type of message get the configuration item
# either from configurationItem in the invoking event
# or using the getResourceConfigHistiry API in getConfiguration function.
def get_configuration_item(invokingEvent):
    check_defined(invokingEvent, 'invokingEvent')
    if is_oversized_changed_notification(invokingEvent['messageType']):
        configurationItemSummary = check_defined(invokingEvent['configurationItemSummary'], 'configurationItemSummary')
        return get_configuration(configurationItemSummary['resourceType'], configurationItemSummary['resourceId'], configurationItemSummary['configurationItemCaptureTime'])
    elif is_scheduled_notification(invokingEvent['messageType']):
        return None
    return check_defined(invokingEvent['configurationItem'], 'configurationItem')

# Check whether the resource has been deleted. If it has, then the evaluation is unnecessary.
def is_applicable(configurationItem, event):
    try:
        check_defined(configurationItem, 'configurationItem')
        check_defined(event, 'event')
    except:
        return True
    status = configurationItem['configurationItemStatus']
    eventLeftScope = event['eventLeftScope']
    if status == 'ResourceDeleted':
        print("Resource Deleted, setting Compliance Status to NOT_APPLICABLE.")
    return (status == 'OK' or status == 'ResourceDiscovered') and not eventLeftScope

def get_assume_role_credentials(role_arn):
    sts_client = boto3.client('sts')
    try:
        assume_role_response = sts_client.assume_role(RoleArn=role_arn, RoleSessionName="configLambdaExecution")
        return assume_role_response['Credentials']
    except botocore.exceptions.ClientError as ex:
        # Scrub error message for any internal account info leaks
        print(str(ex))
        if 'AccessDenied' in ex.response['Error']['Code']:
            ex.response['Error']['Message'] = "AWS Config does not have permission to assume the IAM role."
        else:
            ex.response['Error']['Message'] = "InternalError"
            ex.response['Error']['Code'] = "InternalError"
        raise ex

# This removes older evaluation (usually useful for periodic rule not reporting on AWS::::Account).
def clean_up_old_evaluations(latest_evaluations, event):

    cleaned_evaluations = []

    old_eval = AWS_CONFIG_CLIENT.get_compliance_details_by_config_rule(
        ConfigRuleName=event['configRuleName'],
        ComplianceTypes=['COMPLIANT', 'NON_COMPLIANT'],
        Limit=100)

    old_eval_list = []

    while True:
        for old_result in old_eval['EvaluationResults']:
            old_eval_list.append(old_result)
        if 'NextToken' in old_eval:
            next_token = old_eval['NextToken']
            old_eval = AWS_CONFIG_CLIENT.get_compliance_details_by_config_rule(
                ConfigRuleName=event['configRuleName'],
                ComplianceTypes=['COMPLIANT', 'NON_COMPLIANT'],
                Limit=100,
                NextToken=next_token)
        else:
            break

    for old_eval in old_eval_list:
        old_resource_id = old_eval['EvaluationResultIdentifier']['EvaluationResultQualifier']['ResourceId']
        newer_founded = False
        for latest_eval in latest_evaluations:
            if old_resource_id == latest_eval['ComplianceResourceId']:
                newer_founded = True
        if not newer_founded:
            cleaned_evaluations.append(build_evaluation(old_resource_id, "NOT_APPLICABLE", event))

    return cleaned_evaluations + latest_evaluations

# This decorates the lambda_handler in rule_code with the actual PutEvaluation call
def lambda_handler(event, context):

    global AWS_CONFIG_CLIENT

    #print(event)
    check_defined(event, 'event')
    invoking_event = json.loads(event['invokingEvent'])
    rule_parameters = {}
    if 'ruleParameters' in event:
        rule_parameters = json.loads(event['ruleParameters'])

    try:
        valid_rule_parameters = evaluate_parameters(rule_parameters)
    except ValueError as ex:
        return build_parameters_value_error_response(ex)

    try:
        AWS_CONFIG_CLIENT = get_client('config', event)
        if invoking_event['messageType'] in ['ConfigurationItemChangeNotification', 'ScheduledNotification', 'OversizedConfigurationItemChangeNotification']:
            configuration_item = get_configuration_item(invoking_event)
            if is_applicable(configuration_item, event):
                compliance_result = evaluate_compliance(event, configuration_item, valid_rule_parameters)
            else:
                compliance_result = "NOT_APPLICABLE"
        else:
            return build_internal_error_response('Unexpected message type', str(invoking_event))
    except botocore.exceptions.ClientError as ex:
        if is_internal_error(ex):
            return build_internal_error_response("Unexpected error while completing API request", str(ex))
        return build_error_response("Customer error while making API request", str(ex), ex.response['Error']['Code'], ex.response['Error']['Message'])
    except ValueError as ex:
        return build_internal_error_response(str(ex), str(ex))

    evaluations = []
    latest_evaluations = []

    if not compliance_result:
        latest_evaluations.append(build_evaluation(event['accountId'], "NOT_APPLICABLE", event, resource_type='AWS::::Account'))
        evaluations = clean_up_old_evaluations(latest_evaluations, event)
    elif isinstance(compliance_result, str):
        if configuration_item:
            evaluations.append(build_evaluation_from_config_item(configuration_item, compliance_result))
        else:
            evaluations.append(build_evaluation(event['accountId'], compliance_result, event, resource_type=DEFAULT_RESOURCE_TYPE))
    elif isinstance(compliance_result, list):
        for evaluation in compliance_result:
            missing_fields = False
            for field in ('ComplianceResourceType', 'ComplianceResourceId', 'ComplianceType', 'OrderingTimestamp'):
                if field not in evaluation:
                    print("Missing " + field + " from custom evaluation.")
                    missing_fields = True

            if not missing_fields:
                latest_evaluations.append(evaluation)
        evaluations = clean_up_old_evaluations(latest_evaluations, event)
    elif isinstance(compliance_result, dict):
        missing_fields = False
        for field in ('ComplianceResourceType', 'ComplianceResourceId', 'ComplianceType', 'OrderingTimestamp'):
            if field not in compliance_result:
                print("Missing " + field + " from custom evaluation.")
                missing_fields = True
        if not missing_fields:
            evaluations.append(compliance_result)
    else:
        evaluations.append(build_evaluation_from_config_item(configuration_item, 'NOT_APPLICABLE'))

    # Put together the request that reports the evaluation status
    resultToken = event['resultToken']
    testMode = False
    if resultToken == 'TESTMODE':
        # Used solely for RDK test to skip actual put_evaluation API call
        testMode = True
    # Invoke the Config API to report the result of the evaluation
    AWS_CONFIG_CLIENT.put_evaluations(Evaluations=evaluations, ResultToken=resultToken, TestMode=testMode)
    # Used solely for RDK test to be able to test Lambda function
    return evaluations

def is_internal_error(exception):
    return ((not isinstance(exception, botocore.exceptions.ClientError)) or exception.response['Error']['Code'].startswith('5')
            or 'InternalError' in exception.response['Error']['Code'] or 'ServiceError' in exception.response['Error']['Code'])

def build_internal_error_response(internalErrorMessage, internalErrorDetails=None):
    return build_error_response(internalErrorMessage, internalErrorDetails, 'InternalError', 'InternalError')

def build_error_response(internalErrorMessage, internalErrorDetails=None, customerErrorCode=None, customerErrorMessage=None):
    error_response = {
        'internalErrorMessage': internalErrorMessage,
        'internalErrorDetails': internalErrorDetails,
        'customerErrorMessage': customerErrorMessage,
        'customerErrorCode': customerErrorCode
    }
    print(error_response)
    return error_response
