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
      "name": "2. API Call",
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
            "node": "2. API Call",
            "type": "main",
            "index": 0
          }
        ]
      ]
    },
    "2. API Call": {
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