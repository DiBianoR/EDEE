{
  "nodes": [
    {
      "parameters": {
        "conditions": {
          "options": {
            "caseSensitive": true,
            "leftValue": "",
            "typeValidation": "strict",
            "version": 2
          },
          "conditions": [
            {
              "id": "c1",
              "leftValue": "={{ $json.skipApi }}",
              "rightValue": true,
              "operator": {
                "type": "boolean",
                "operation": "equals"
              }
            }
          ],
          "combinator": "and"
        },
        "options": {}
      },
      "type": "n8n-nodes-base.if",
      "typeVersion": 2.2,
      "position": [
        624,
        336
      ],
      "id": "32fcd50a-00a3-4d48-944c-0c470946c075",
      "name": "Skip API Call?"
    },
    {
      "parameters": {
        "rules": {
          "values": [
            {
              "conditions": {
                "options": {
                  "caseSensitive": true,
                  "leftValue": "",
                  "typeValidation": "strict",
                  "version": 2
                },
                "conditions": [
                  {
                    "id": "s1",
                    "leftValue": "={{ $json.auth_mode }}",
                    "rightValue": "query_key",
                    "operator": {
                      "type": "string",
                      "operation": "equals"
                    }
                  }
                ],
                "combinator": "and"
              },
              "renameOutput": true,
              "outputKey": "query_key"
            },
            {
              "conditions": {
                "options": {
                  "caseSensitive": true,
                  "leftValue": "",
                  "typeValidation": "strict",
                  "version": 2
                },
                "conditions": [
                  {
                    "id": "s2",
                    "leftValue": "={{ $json.auth_mode }}",
                    "rightValue": "bearer",
                    "operator": {
                      "type": "string",
                      "operation": "equals"
                    }
                  }
                ],
                "combinator": "and"
              },
              "renameOutput": true,
              "outputKey": "bearer"
            }
          ]
        },
        "options": {}
      },
      "type": "n8n-nodes-base.switch",
      "typeVersion": 3.2,
      "position": [
        624,
        608
      ],
      "id": "678fb777-685e-4cf0-9b32-728e57494573",
      "name": "Provider Switch"
    },
    {
      "parameters": {
        "method": "POST",
        "url": "={{ $json.model_url }}",
        "sendHeaders": true,
        "headerParameters": {
          "parameters": [
            {
              "name": "Content-Type",
              "value": "application/json"
            },
            {
              "name": "Authorization",
              "value": "=Bearer {{ $json.api_key }}"
            }
          ]
        },
        "sendBody": true,
        "specifyBody": "json",
        "jsonBody": "={{ $json.requestBody }}",
        "options": {
          "timeout": 180000
        }
      },
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [
        848,
        688
      ],
      "id": "6d538c70-b9c8-4dd0-a816-382da89b4888",
      "name": "2b. API Call (OpenAI)",
      "retryOnFail": true,
      "maxTries": 3,
      "waitBetweenTries": 2000
    },
    {
      "parameters": {
        "method": "POST",
        "url": "={{ $json.model_url }}",
        "sendQuery": true,
        "queryParameters": {
          "parameters": [
            {
              "name": "key",
              "value": "={{ $json.api_key }}"
            }
          ]
        },
        "sendHeaders": true,
        "headerParameters": {
          "parameters": [
            {
              "name": "Content-Type",
              "value": "application/json"
            }
          ]
        },
        "sendBody": true,
        "specifyBody": "json",
        "jsonBody": "={{ $json.requestBody }}",
        "options": {
          "timeout": 180000
        }
      },
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [
        848,
        528
      ],
      "id": "bf382e6b-8e23-4569-be35-b8feab8c2948",
      "name": "2. API Call(Gemini)",
      "retryOnFail": true,
      "maxTries": 3,
      "waitBetweenTries": 2000
    }
  ],
  "connections": {
    "Skip API Call?": {
      "main": [
        [],
        [
          {
            "node": "Provider Switch",
            "type": "main",
            "index": 0
          }
        ]
      ]
    },
    "Provider Switch": {
      "main": [
        [
          {
            "node": "2. API Call(Gemini)",
            "type": "main",
            "index": 0
          }
        ],
        [
          {
            "node": "2b. API Call (OpenAI)",
            "type": "main",
            "index": 0
          }
        ]
      ]
    },
    "2b. API Call (OpenAI)": {
      "main": [
        []
      ]
    },
    "2. API Call(Gemini)": {
      "main": [
        []
      ]
    }
  },
  "pinData": {},
  "meta": {
    "instanceId": "050d7f5d3593566ffe36464e2179832d9e4aa282cb8a519a27bd68446dae16b9"
  }
}