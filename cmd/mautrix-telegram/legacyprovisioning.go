// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2024 Sumner Evans
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sync"

	"github.com/gorilla/websocket"
	"github.com/rs/zerolog"
	"go.mau.fi/util/exhttp"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/id"

	"go.mau.fi/mautrix-telegram/pkg/connector"
)

type response struct {
	Username id.UserID `json:"username,omitempty"`
	State    string    `json:"state,omitempty"`
	Message  string    `json:"message,omitempty"`
	Error    string    `json:"error,omitempty"`
	ErrCode  string    `json:"errcode,omitempty"`
}

func (r response) WithState(state string) response {
	r.State = state
	return r
}

func (r response) WithMessage(message string) response {
	r.Message = message
	return r
}

func (r response) WithError(errCode, error string) response {
	r.ErrCode = errCode
	r.Error = error
	return r
}

type legacyLogin struct {
	Process  bridgev2.LoginProcess
	NextStep *bridgev2.LoginStep
}

var inflightLegacyLoginsLock sync.RWMutex
var inflightLegacyLogins = map[id.UserID]*legacyLogin{}

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool {
		return true
	},
	Subprotocols: []string{"net.maunium.telegram.login"},
}

func legacyProvLoginQR(w http.ResponseWriter, r *http.Request) {
	log := zerolog.Ctx(r.Context()).With().Str("prov_method", "qr_login").Logger()
	ctx := log.WithContext(r.Context())

	user := m.Matrix.Provisioning.GetUser(r)
	resp := response{Username: user.MXID}

	var err error
	var loginProcess bridgev2.LoginProcess
	var nextStep *bridgev2.LoginStep
	if loginProcess, err = c.CreateLogin(ctx, user, connector.LoginFlowIDQR); err != nil {
		exhttp.WriteJSONResponse(w, http.StatusInternalServerError, resp.WithError("create_login_failed", fmt.Sprintf("Failed to create a QR login process: %s", err.Error())))
	} else if nextStep, err = loginProcess.Start(ctx); err != nil {
		exhttp.WriteJSONResponse(w, http.StatusInternalServerError, resp.WithError("start_login_failed", fmt.Sprintf("Failed to start login process: %s", err.Error())))
	} else if nextStep.StepID != connector.LoginStepIDShowQR {
		exhttp.WriteJSONResponse(w, http.StatusInternalServerError, resp.WithError("unexpected_step", fmt.Sprintf("Unexpected first step %s", nextStep.StepID)))
	}

	ws, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Err(err).Msg("Failed to upgrade connection to websocket")
		return
	}
	defer func() {
		err := ws.Close()
		if err != nil {
			log.Debug().Err(err).Msg("Error closing websocket")
		}
	}()

	go func() {
		// Read everything so SetCloseHandler() works
		for {
			_, _, err = ws.ReadMessage()
			if err != nil {
				break
			}
		}
	}()
	ctx, cancel := context.WithCancel(context.Background())
	ws.SetCloseHandler(func(code int, text string) error {
		log.Debug().Int("close_code", code).Msg("Login websocket closed, cancelling login")
		cancel()
		return nil
	})

	for {
		switch nextStep.StepID {
		case connector.LoginStepIDShowQR:
			nextStep, err = loginProcess.(bridgev2.LoginProcessDisplayAndWait).Wait(ctx)
			if err != nil {
				ws.WriteJSON(map[string]any{
					"success": false,
					"error":   "qr_login_failed",
					"message": fmt.Sprintf("Failed to login using QR code: %s", err),
				})
				return
			}
			ws.WriteJSON(map[string]any{"code": nextStep.DisplayAndWaitParams.Data})
		case connector.LoginStepIDComplete:
			ws.WriteJSON(map[string]any{"success": true})
			return
		case connector.LoginStepIDPassword:
			inflightLegacyLoginsLock.Lock()
			inflightLegacyLogins[user.MXID] = &legacyLogin{Process: loginProcess, NextStep: nextStep}
			inflightLegacyLoginsLock.Unlock()
			ws.WriteJSON(map[string]any{"success": false, "error": "password-needed"})
			return
		default:
			ws.WriteJSON(map[string]any{
				"success": false,
				"error":   "unexpected_step",
				"message": fmt.Sprintf("Unexpected step in QR code login process %s", nextStep.StepID),
			})
			return
		}
	}
}

func legacyProvLoginRequestCode(w http.ResponseWriter, r *http.Request) {
	log := zerolog.Ctx(r.Context()).With().Str("prov_step", "request_code").Logger()
	ctx := log.WithContext(r.Context())

	user := m.Matrix.Provisioning.GetUser(r)
	resp := response{Username: user.MXID, State: "request"}

	legacyProvRequestCodeReq := map[string]string{}
	if err := json.NewDecoder(r.Body).Decode(&legacyProvRequestCodeReq); err != nil {
		exhttp.WriteJSONResponse(w, http.StatusBadRequest, resp.WithError("request_body_invalid", "Request body is invalid"))
	} else if phone, ok := legacyProvRequestCodeReq["phone"]; !ok || phone == "" {
		exhttp.WriteJSONResponse(w, http.StatusBadRequest, resp.WithError("phone_missing", "Phone number missing"))
	} else if loginProcess, err := c.CreateLogin(ctx, user, connector.LoginFlowIDPhone); err != nil {
		exhttp.WriteJSONResponse(w, http.StatusInternalServerError, resp.WithError("create_login_failed", fmt.Sprintf("Failed to create a phone number login process: %s", err.Error())))
	} else if firstStep, err := loginProcess.Start(ctx); err != nil {
		exhttp.WriteJSONResponse(w, http.StatusInternalServerError, resp.WithError("start_login_failed", fmt.Sprintf("Failed to start login process: %s", err.Error())))
	} else if firstStep.StepID != connector.LoginStepIDPhoneNumber {
		exhttp.WriteJSONResponse(w, http.StatusInternalServerError, resp.WithError("unexpected_step", fmt.Sprintf("Unexpected first step %s", firstStep.StepID)))
	} else if nextStep, err := loginProcess.(bridgev2.LoginProcessUserInput).SubmitUserInput(ctx, map[string]string{connector.LoginStepIDPhoneNumber: phone}); err != nil {
		exhttp.WriteJSONResponse(w, http.StatusBadRequest, resp.WithError("request_code_failed", fmt.Sprintf("Failed to request code: %s", err.Error())))
	} else if nextStep.StepID != connector.LoginStepIDCode {
		exhttp.WriteJSONResponse(w, http.StatusInternalServerError, resp.WithError("unexpected_step", fmt.Sprintf("Unexpected step %s", nextStep.StepID)))
	} else {
		inflightLegacyLoginsLock.Lock()
		inflightLegacyLogins[user.MXID] = &legacyLogin{
			Process:  loginProcess,
			NextStep: nextStep,
		}
		inflightLegacyLoginsLock.Unlock()
		exhttp.WriteJSONResponse(w, http.StatusOK, resp.
			WithState("code").
			WithMessage("Code requested successfully. Check your SMS or Telegram app and enter the code below."),
		)
	}
}

func legacyProvLoginSendCode(w http.ResponseWriter, r *http.Request) {
	log := zerolog.Ctx(r.Context()).With().Str("prov_step", "send_code").Logger()
	ctx := log.WithContext(r.Context())

	user := m.Matrix.Provisioning.GetUser(r)
	resp := response{Username: user.MXID, State: "code"}

	legacyProvSendCodeReq := map[string]string{}
	if inflightLogin, ok := inflightLegacyLogins[user.MXID]; !ok {
		exhttp.WriteJSONResponse(w, http.StatusBadRequest, resp.WithError("no_login", "No login process in progress"))
	} else if inflightLogin.NextStep.StepID != connector.LoginStepIDCode {
		exhttp.WriteJSONResponse(w, http.StatusBadRequest, resp.WithError("unexpected_step", fmt.Sprintf("Unexpected step %s", inflightLogin.NextStep.StepID)))
	} else if err := json.NewDecoder(r.Body).Decode(&legacyProvSendCodeReq); err != nil {
		exhttp.WriteJSONResponse(w, http.StatusBadRequest, resp.WithError("request_body_invalid", "Request body is invalid"))
	} else if code, ok := legacyProvSendCodeReq["code"]; !ok || code == "" {
		exhttp.WriteJSONResponse(w, http.StatusBadRequest, resp.WithError("phone_code_missing", "You must provide the code from your phone."))
	} else if nextStep, err := inflightLogin.Process.(bridgev2.LoginProcessUserInput).SubmitUserInput(ctx, map[string]string{connector.LoginStepIDCode: code}); err != nil {
		exhttp.WriteJSONResponse(w, http.StatusBadRequest, resp.WithError("send_code_failed", fmt.Sprintf("Failed to send code: %s", err.Error())))
	} else if nextStep.StepID == connector.LoginStepIDPassword {
		inflightLegacyLoginsLock.Lock()
		defer inflightLegacyLoginsLock.Unlock()
		inflightLegacyLogins[user.MXID] = &legacyLogin{
			Process:  inflightLogin.Process,
			NextStep: nextStep,
		}
		exhttp.WriteJSONResponse(w, http.StatusAccepted, resp.
			WithState("password").
			WithMessage("Code accepted, but you have 2-factor authentication enabled. Please enter your password."),
		)
		return // Don't delete the inflight login yet, we need to submit the password.
	} else if nextStep.StepID == connector.LoginStepIDComplete {
		exhttp.WriteJSONResponse(w, http.StatusOK, resp.WithState("logged-in"))
	} else {
		exhttp.WriteJSONResponse(w, http.StatusInternalServerError, resp.WithError("unexpected_step", fmt.Sprintf("Unexpected step %s", nextStep.StepID)))
	}

	// If we got here, then there was an error, or the login is complete.
	// Delete the in-flight login.
	inflightLegacyLoginsLock.Lock()
	delete(inflightLegacyLogins, user.MXID)
	inflightLegacyLoginsLock.Unlock()
}

func legacyProvLoginSendPassword(w http.ResponseWriter, r *http.Request) {
	log := zerolog.Ctx(r.Context()).With().Str("prov_step", "send_password").Logger()
	ctx := log.WithContext(r.Context())

	user := m.Matrix.Provisioning.GetUser(r)
	resp := response{Username: user.MXID, State: "password"}

	legacyProvSendPasswordReq := map[string]string{}
	if inflightLogin, ok := inflightLegacyLogins[user.MXID]; !ok {
		exhttp.WriteJSONResponse(w, http.StatusBadRequest, resp.WithError("no_login", "No login process in progress"))
	} else if inflightLogin.NextStep.StepID != connector.LoginStepIDPassword {
		exhttp.WriteJSONResponse(w, http.StatusBadRequest, resp.WithError("unexpected_step", fmt.Sprintf("Unexpected step %s", inflightLogin.NextStep.StepID)))
	} else if err := json.NewDecoder(r.Body).Decode(&legacyProvSendPasswordReq); err != nil {
		exhttp.WriteJSONResponse(w, http.StatusBadRequest, resp.WithError("request_body_invalid", "Request body is invalid"))
	} else if password, ok := legacyProvSendPasswordReq["password"]; !ok || password == "" {
		exhttp.WriteJSONResponse(w, http.StatusBadRequest, resp.WithError("password_missing", "You must provide your password."))
	} else if nextStep, err := inflightLogin.Process.(bridgev2.LoginProcessUserInput).SubmitUserInput(ctx, map[string]string{connector.LoginStepIDPassword: password}); err != nil {
		exhttp.WriteJSONResponse(w, http.StatusBadRequest, resp.WithError("send_password_failed", fmt.Sprintf("Failed to send password: %s", err.Error())))
	} else if nextStep.StepID == connector.LoginStepIDComplete {
		exhttp.WriteJSONResponse(w, http.StatusOK, resp.WithState("logged-in"))
	} else {
		exhttp.WriteJSONResponse(w, http.StatusInternalServerError, resp.WithError("unexpected_step", fmt.Sprintf("Unexpected step %s", nextStep.StepID)))
	}

	// If we got here, then there was an error, or the login is complete.
	// Delete the in-flight login.
	inflightLegacyLoginsLock.Lock()
	delete(inflightLegacyLogins, user.MXID)
	inflightLegacyLoginsLock.Unlock()
}

func legacyProvLogout(w http.ResponseWriter, r *http.Request) {
	user := m.Matrix.Provisioning.GetUser(r)
	logins := user.GetUserLogins()
	for _, login := range logins {
		login.Logout(r.Context())
	}
	exhttp.WriteEmptyJSONResponse(w, http.StatusOK)
}
