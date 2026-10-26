/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef CHIRON_MENU_TOUCH_H
#define CHIRON_MENU_TOUCH_H

#include <Protocol/AbsolutePointer.h>

EFI_STATUS ChironMenuTouchStart (VOID);
EFI_STATUS ChironMenuTouchPoll (EFI_ABSOLUTE_POINTER_STATE *State);
EFI_STATUS ChironMenuTouchStop (VOID);
VOID ChironMenuTouchStatus (CHAR16 *Line, UINTN Bytes);
VOID ChironMenuTouchFrameStatus (CHAR16 *Line, UINTN Bytes);

#endif
