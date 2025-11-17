/*
 * Copyright 2010-2016 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License").
 * You may not use this file except in compliance with the License.
 * A copy of the License is located at
 *
 *  http://aws.amazon.com/apache2.0
 *
 * or in the "license" file accompanying this file. This file is distributed
 * on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
 * express or implied. See the License for the specific language governing
 * permissions and limitations under the License.
 */

var apigClientFactory = {};
apigClientFactory.newClient = function (config) {
    var apigClient = { };
    if(config === undefined) {
        config = {
            accessKey: '',
            secretKey: '',
            sessionToken: '',
            region: '',
            apiKey: undefined,
            defaultContentType: 'application/json',
            defaultAcceptType: 'application/json'
        };
    }
    if(config.accessKey === undefined) {
        config.accessKey = '';
    }
    if(config.secretKey === undefined) {
        config.secretKey = '';
    }
    if(config.apiKey === undefined) {
        config.apiKey = '';
    }
    if(config.sessionToken === undefined) {
        config.sessionToken = '';
    }
    if(config.region === undefined) {
        config.region = 'us-east-1';
    }
    //If defaultContentType is not defined then default to application/json
    if(config.defaultContentType === undefined) {
        config.defaultContentType = 'application/json';
    }
    //If defaultAcceptType is not defined then default to application/json
    if(config.defaultAcceptType === undefined) {
        config.defaultAcceptType = 'application/json';
    }

    
    // extract endpoint and path from url
    var invokeUrl = 'https://uxojfee0s4.execute-api.us-east-1.amazonaws.com/cloud-hw1-stage';
    var endpoint = /(^https?:\/\/[^\/]+)/g.exec(invokeUrl)[1];
    var pathComponent = invokeUrl.substring(endpoint.length);

    var sigV4ClientConfig = {
        accessKey: config.accessKey,
        secretKey: config.secretKey,
        sessionToken: config.sessionToken,
        serviceName: 'execute-api',
        region: config.region,
        endpoint: endpoint,
        defaultContentType: config.defaultContentType,
        defaultAcceptType: config.defaultAcceptType
    };

    var authType = 'NONE';
    if (sigV4ClientConfig.accessKey !== undefined && sigV4ClientConfig.accessKey !== '' && sigV4ClientConfig.secretKey !== undefined && sigV4ClientConfig.secretKey !== '') {
        authType = 'AWS_IAM';
    }

    var simpleHttpClientConfig = {
        endpoint: endpoint,
        defaultContentType: config.defaultContentType,
        defaultAcceptType: config.defaultAcceptType
    };

    var apiGatewayClient = apiGateway.core.apiGatewayClientFactory.newClient(simpleHttpClientConfig, sigV4ClientConfig);
    
    
    
    // apigClient.chatbotPost = function (params, body, additionalParams) {
    //     if(additionalParams === undefined) { additionalParams = {}; }
        
    //     // apiGateway.core.utils.assertParametersDefined(params, ['body'], ['body']);
    //     apiGateway.core.utils.assertParametersDefined(params, [], ['body']);
        
    //     var chatbotPostRequest = {
    //         verb: 'post'.toUpperCase(),
    //         path: pathComponent + uritemplate('/chatbot').expand(apiGateway.core.utils.parseParametersToObject(params, [])),
    //         headers: apiGateway.core.utils.parseParametersToObject(params, []),
    //         queryParams: apiGateway.core.utils.parseParametersToObject(params, []),
    //         body: body
    //     };
        
        
    //     return apiGatewayClient.makeRequest(chatbotPostRequest, authType, additionalParams, config.apiKey);
    // };

    apigClient.chatbotPost = function (params, body, additionalParams) {
        if (params === undefined) params = {};
        if (body   === undefined) body   = {};
        if (additionalParams === undefined) additionalParams = {};
        if (!additionalParams.headers) additionalParams.headers = {};
        if (!additionalParams.headers['Content-Type']) additionalParams.headers['Content-Type'] = 'application/json';
        if (!additionalParams.headers['Accept'])       additionalParams.headers['Accept']       = 'application/json';

        // ---- normalize request payload (accept Lex Web UI & simple forms) ----
        var payload = body;
        if (typeof payload === 'string') { try { payload = JSON.parse(payload); } catch { payload = { message: String(body) }; } }
        if (payload && payload.messages && Array.isArray(payload.messages) && payload.messages.length > 0) {
            const m0 = payload.messages[0] || {};
            const txt = (m0.unstructured && m0.unstructured.text) || m0.text || m0.content;
            if (txt != null && txt !== '') payload = { message: txt };
        }
        if (payload.message == null) {
            if (payload.text != null) payload.message = payload.text;
            else if (params.message != null) payload.message = params.message;
            else if (params.text != null)    payload.message = params.text;
            else if (params.userMessage != null) payload.message = params.userMessage;
        }

        // ---- stable sessionId across turns ----
        var sid = null;
        try { sid = localStorage.getItem('lexSessionId'); } catch (e) {}
        // Prefer existing sid; if caller provided one, use it and also persist below
        if (!sid && payload.sessionId) sid = payload.sessionId;

        // send via query param (and also echo in body to be safe)
        var queryParams = {};
        if (sid) {
            queryParams.sessionId = sid;
            payload.sessionId     = sid;
        }

        const request = {
            verb: 'post'.toUpperCase(),
            path: pathComponent + uritemplate('/chatbot').expand({}),
            headers: {},
            queryParams: {},
            body: payload
        };

        return apiGatewayClient
            .makeRequest(request, authType, additionalParams, config.apiKey)
            .then(function (res) {
                // Persist sessionId returned by the backend for next turns
                if (res && res.data && res.data.sessionId) {
                    try { localStorage.setItem('lexSessionId', res.data.sessionId); } catch (e) {}
                }
                // ---- normalize response for your UI ----
                if (res && res.data && res.data.message) {
                    const text = res.data.message;
                    // keep original fields
                    res.data.reply       = res.data.reply       || text;
                    res.data.botResponse = res.data.botResponse || text;
                    // add Lex Web UI-style envelope your chat UI expects
                    res.data.messages = res.data.messages || [
                    { type: 'unstructured', unstructured: { text: text } }
                    ];
                }
            return res;
        });
    };



    
    apigClient.chatbotOptions = function (params, body, additionalParams) {
        if(additionalParams === undefined) { additionalParams = {}; }
        
        apiGateway.core.utils.assertParametersDefined(params, [], ['body']);
        
        var chatbotOptionsRequest = {
            verb: 'options'.toUpperCase(),
            path: pathComponent + uritemplate('/chatbot').expand(apiGateway.core.utils.parseParametersToObject(params, [])),
            headers: apiGateway.core.utils.parseParametersToObject(params, []),
            queryParams: apiGateway.core.utils.parseParametersToObject(params, []),
            body: body
        };
        
        
        return apiGatewayClient.makeRequest(chatbotOptionsRequest, authType, additionalParams, config.apiKey);
    };
    

    return apigClient;
};
