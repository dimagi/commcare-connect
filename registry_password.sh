#!/bin/bash
VAULT_ID="Connect Tech"
VAULT_ANSIBLE_NAME="Ansible Vault"
op item get --vault="$VAULT_ID" "$VAULT_ANSIBLE_NAME" --fields password
