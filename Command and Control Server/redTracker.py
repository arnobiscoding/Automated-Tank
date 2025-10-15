import cv2
import numpy as np
import time

# --- Initialize webcam ---
cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

prev_time = time.time()
last_cx, last_cy = None, None

print("🎯 Starting Red Object Tracker... Press 'q' to quit.")

while True:
    ret, frame = cap.read()
    if not ret:
        break

    # --- Preprocessing ---
    frame_blur = cv2.GaussianBlur(frame, (7, 7), 0)
    hsv = cv2.cvtColor(frame_blur, cv2.COLOR_BGR2HSV)
    h, w, _ = frame.shape
    center_x, center_y = w // 2, h // 2

    # --- Define red color range in HSV ---
    lower_red1 = np.array([0, 100, 70])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([170, 170, 120])
    upper_red2 = np.array([180, 255, 255])

    # --- Create mask and clean it ---
    mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
    mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
    mask = cv2.bitwise_or(mask1, mask2)
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    # --- Find contours ---
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    target_contour = None
    max_area = 0

    for contour in contours:
        area = cv2.contourArea(contour)
        if area > 500:
            if area > max_area:
                max_area = area
                target_contour = contour
            cv2.drawContours(frame, [contour], -1, (0, 255, 0), 1)

    # --- Draw camera center ---
    cv2.circle(frame, (center_x, center_y), 6, (0, 255, 255), -1)
    cv2.line(frame, (center_x - 20, center_y), (center_x + 20, center_y), (0, 255, 255), 1)
    cv2.line(frame, (center_x, center_y - 20), (center_x, center_y + 20), (0, 255, 255), 1)

    direction = "No Target"
    cx, cy = None, None

    if target_contour is not None:
        # Bounding box and centroid
        x, y, w_box, h_box = cv2.boundingRect(target_contour)
        cv2.rectangle(frame, (x, y), (x + w_box, y + h_box), (255, 0, 0), 3)

        M = cv2.moments(target_contour)
        if M["m00"] != 0:
            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])
            last_cx, last_cy = cx, cy
            cv2.circle(frame, (cx, cy), 6, (0, 0, 255), -1)
            cv2.putText(frame, "Target", (cx - 30, cy - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

            # --- Determine direction feedback ---
            dx = cx - center_x
            dy = cy - center_y
            radius = 50  # tolerance for being “centered”

            if abs(dx) < radius and abs(dy) < radius:
                direction = "Centered ✅"
            elif abs(dx) > abs(dy):
                direction = "⬅ Move LEFT" if dx < 0 else "➡ Move RIGHT"
            else:
                direction = "⬇ Move DOWN" if dy < 0 else "⬆ Move UP"

            # --- Draw arrows for direction ---
            if direction == "⬅ Move LEFT":
                cv2.arrowedLine(frame, (center_x, center_y),
                                (center_x - 100, center_y), (0, 0, 255), 3)
            elif direction == "➡ Move RIGHT":
                cv2.arrowedLine(frame, (center_x, center_y),
                                (center_x + 100, center_y), (0, 0, 255), 3)
            elif direction == "⬆ Move UP":
                cv2.arrowedLine(frame, (center_x, center_y),
                                (center_x, center_y + 100), (0, 0, 255), 3)
            elif direction == "⬇ Move DOWN":
                cv2.arrowedLine(frame, (center_x, center_y),
                                (center_x, center_y - 100), (0, 0, 255), 3)

            # --- Estimate distance based on area ---
            distance = int(50000 / (max_area ** 0.5))
            cv2.putText(frame, f"Distance: {distance}", (30, 90),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

    elif last_cx is not None:
        # Draw last seen position
        cv2.circle(frame, (last_cx, last_cy), 6, (255, 255, 0), -1)
        cv2.putText(frame, "Last seen", (last_cx - 40, last_cy - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 2)

    # --- Show direction text ---
    cv2.putText(frame, direction, (30, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 3, cv2.LINE_AA)
    cv2.putText(frame, direction, (30, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 1, cv2.LINE_AA)

    # --- FPS Counter ---
    curr_time = time.time()
    fps = 1 / (curr_time - prev_time)
    prev_time = curr_time
    cv2.putText(frame, f"FPS: {int(fps)}", (520, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

    # --- Display Frame ---
    cv2.imshow("Red Object Tracking (Enhanced)", frame)

    # Exit key
    key = cv2.waitKey(1) & 0xFF
    if key == ord('q') or key == 27:  # q or ESC
        break

cap.release()
cv2.destroyAllWindows()
