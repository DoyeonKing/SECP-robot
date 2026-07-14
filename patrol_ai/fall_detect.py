import argparse
import json
import sys
import time
from pathlib import Path


PERSON_CLASS_ID = 0


def compute_aspect_ratio(bbox):
    x1, y1, x2, y2 = bbox
    width = max(0.0, float(x2) - float(x1))
    height = max(1.0, float(y2) - float(y1))
    return width / height


def box_area(bbox):
    x1, y1, x2, y2 = bbox
    return max(0.0, float(x2) - float(x1)) * max(0.0, float(y2) - float(y1))


def posture_features(bbox, frame_shape):
    frame_h = max(1.0, float(frame_shape[0]))
    frame_w = max(1.0, float(frame_shape[1]))
    x1, y1, x2, y2 = [float(v) for v in bbox]
    width = max(0.0, x2 - x1)
    height = max(1.0, y2 - y1)
    return {
        "aspect_ratio": width / height,
        "center_y": ((y1 + y2) / 2.0) / frame_h,
        "bottom_y": y2 / frame_h,
        "height_ratio": height / frame_h,
        "width_ratio": width / frame_w,
    }


def is_low_posture(
    bbox,
    frame_shape,
    center_y_threshold=0.55,
    bottom_y_threshold=0.68,
    max_height_ratio=0.75,
    min_aspect_ratio=0.35,
):
    features = posture_features(bbox, frame_shape)
    return (
        features["center_y"] >= center_y_threshold
        and features["bottom_y"] >= bottom_y_threshold
        and features["height_ratio"] <= max_height_ratio
        and features["aspect_ratio"] >= min_aspect_ratio
    )


def video_event_time(vid_cap, fallback_now):
    if vid_cap is None:
        return fallback_now
    try:
        pos_msec = float(vid_cap.get(0))
    except Exception:
        return fallback_now
    if pos_msec <= 0:
        return fallback_now
    return pos_msec / 1000.0


def choose_largest_person(detections, min_confidence):
    people = [
        det
        for det in detections
        if int(det["class_id"]) == PERSON_CLASS_ID
        and float(det["confidence"]) >= float(min_confidence)
    ]
    if not people:
        return None
    return max(people, key=lambda det: box_area(det["bbox"]))


class FallState:
    def __init__(self, ratio_threshold=1.3, duration_threshold=1.2):
        self.ratio_threshold = float(ratio_threshold)
        self.duration_threshold = float(duration_threshold)
        self.abnormal_since = None
        self.abnormal_reason = "normal"

    def update(self, now, person_detected, aspect_ratio, low_posture=False):
        horizontal = (
            person_detected
            and aspect_ratio is not None
            and float(aspect_ratio) >= self.ratio_threshold
        )
        abnormal = horizontal or (person_detected and bool(low_posture))

        if not abnormal:
            self.abnormal_since = None
            self.abnormal_reason = "normal"
            return False

        if self.abnormal_since is None:
            self.abnormal_since = float(now)
            self.abnormal_reason = "horizontal" if horizontal else "low_posture"
            return False

        self.abnormal_reason = "horizontal" if horizontal else "low_posture"
        return float(now) - self.abnormal_since >= self.duration_threshold

    def abnormal_duration(self, now):
        if self.abnormal_since is None:
            return 0.0
        return max(0.0, float(now) - self.abnormal_since)


def build_ai_result(person, aspect_ratio, low_posture, fall_alert, fall_state, now):
    person_detected = person is not None
    posture_type = "none"
    if person_detected and aspect_ratio is not None and aspect_ratio >= fall_state.ratio_threshold:
        posture_type = "horizontal"
    elif person_detected and low_posture:
        posture_type = "low_posture"
    elif person_detected:
        posture_type = "upright_or_unknown"

    if fall_alert:
        ai_status = "fall_confirmed"
        risk_level = "high"
        message = "fall-like posture confirmed"
    elif posture_type in ("horizontal", "low_posture"):
        ai_status = "fall_suspected"
        risk_level = "medium"
        message = "fall-like posture is being confirmed"
    elif person_detected:
        ai_status = "person_detected"
        risk_level = "low"
        message = "person detected"
    else:
        ai_status = "no_person"
        risk_level = "low"
        message = "no person detected"

    return {
        "ai_status": ai_status,
        "person_detected": person_detected,
        "fall_alert": bool(fall_alert),
        "confidence": round(float(person["confidence"]), 4) if person else 0.0,
        "bbox": [round(float(v), 2) for v in person["bbox"]] if person else None,
        "aspect_ratio": round(float(aspect_ratio), 4) if aspect_ratio is not None else None,
        "low_posture": bool(low_posture),
        "posture_type": posture_type,
        "abnormal_duration": round(float(fall_state.abnormal_duration(now)), 2),
        "risk_level": risk_level,
        "message": message,
        "timestamp": round(float(now), 3),
    }


def parse_opt():
    parser = argparse.ArgumentParser()
    parser.add_argument("--weights", nargs="+", type=str, default="yolov5s.pt")
    parser.add_argument("--source", type=str, default="0", help="video path, image path, or camera index")
    parser.add_argument("--data", type=str, default="data/coco128.yaml")
    parser.add_argument("--imgsz", "--img", "--img-size", nargs="+", type=int, default=[640])
    parser.add_argument("--conf-thres", type=float, default=0.25)
    parser.add_argument("--iou-thres", type=float, default=0.45)
    parser.add_argument("--max-det", type=int, default=1000)
    parser.add_argument("--device", default="")
    parser.add_argument("--view-img", action="store_true", help="show live window if a display is available")
    parser.add_argument("--save-json", type=str, default="", help="optional jsonl output path")
    parser.add_argument("--nosave", action="store_true", help="do not save annotated video/images")
    parser.add_argument("--project", default="runs/fall_detect")
    parser.add_argument("--name", default="exp")
    parser.add_argument("--exist-ok", action="store_true")
    parser.add_argument("--line-thickness", default=3, type=int)
    parser.add_argument("--hide-labels", default=False, action="store_true")
    parser.add_argument("--hide-conf", default=False, action="store_true")
    parser.add_argument("--half", action="store_true")
    parser.add_argument("--dnn", action="store_true")
    parser.add_argument("--vid-stride", type=int, default=1)
    parser.add_argument("--person-conf", type=float, default=0.5)
    parser.add_argument("--fall-ratio", type=float, default=1.3)
    parser.add_argument("--fall-duration", type=float, default=1.2)
    parser.add_argument("--low-center-y", type=float, default=0.55)
    parser.add_argument("--low-bottom-y", type=float, default=0.68)
    parser.add_argument("--low-max-height", type=float, default=0.75)
    parser.add_argument("--low-min-ratio", type=float, default=0.35)
    parser.add_argument("--print-interval", type=float, default=0.5)
    opt = parser.parse_args()
    opt.imgsz *= 2 if len(opt.imgsz) == 1 else 1
    return opt


def run(
    weights="yolov5s.pt",
    source="0",
    data="data/coco128.yaml",
    imgsz=(640, 640),
    conf_thres=0.25,
    iou_thres=0.45,
    max_det=1000,
    device="",
    view_img=False,
    save_json="",
    nosave=False,
    project="runs/fall_detect",
    name="exp",
    exist_ok=False,
    line_thickness=3,
    hide_labels=False,
    hide_conf=False,
    half=False,
    dnn=False,
    vid_stride=1,
    person_conf=0.5,
    fall_ratio=1.3,
    fall_duration=1.2,
    low_center_y=0.55,
    low_bottom_y=0.68,
    low_max_height=0.75,
    low_min_ratio=0.35,
    print_interval=0.5,
):
    import torch

    root = Path(__file__).resolve().parent
    if str(root) not in sys.path:
        sys.path.append(str(root))

    from models.common import DetectMultiBackend
    from utils.dataloaders import IMG_FORMATS, VID_FORMATS, LoadImages, LoadStreams
    from utils.general import (
        LOGGER,
        Profile,
        check_file,
        check_img_size,
        check_imshow,
        colorstr,
        cv2,
        increment_path,
        non_max_suppression,
        scale_boxes,
    )
    from utils.plots import Annotator, colors
    from utils.torch_utils import select_device

    source = str(source)
    save_img = not nosave
    is_file = Path(source).suffix[1:] in (IMG_FORMATS + VID_FORMATS)
    is_url = source.lower().startswith(("rtsp://", "rtmp://", "http://", "https://"))
    webcam = source.isnumeric() or source.endswith(".txt") or (is_url and not is_file)
    if is_url and is_file:
        source = check_file(source)

    save_dir = increment_path(Path(project) / name, exist_ok=exist_ok)
    if save_img:
        save_dir.mkdir(parents=True, exist_ok=True)

    json_file = None
    if save_json:
        json_path = Path(save_json)
        if not json_path.is_absolute():
            json_path = save_dir / json_path
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_file = json_path.open("w", encoding="utf-8")

    device = select_device(device)
    model = DetectMultiBackend(weights, device=device, dnn=dnn, data=data, fp16=half)
    stride, names, pt = model.stride, model.names, model.pt
    imgsz = check_img_size(imgsz, s=stride)

    bs = 1
    if webcam:
        view_img = check_imshow(warn=True) if view_img else False
        dataset = LoadStreams(source, img_size=imgsz, stride=stride, auto=pt, vid_stride=vid_stride)
        bs = len(dataset)
    else:
        dataset = LoadImages(source, img_size=imgsz, stride=stride, auto=pt, vid_stride=vid_stride)

    model.warmup(imgsz=(1 if pt else bs, 3, *imgsz))
    seen, windows, dt = 0, [], (Profile(), Profile(), Profile())
    vid_path, vid_writer = [None] * bs, [None] * bs
    states = {}
    last_print = {}

    try:
        for path, im, im0s, vid_cap, s in dataset:
            with dt[0]:
                im = torch.from_numpy(im).to(model.device)
                im = im.half() if model.fp16 else im.float()
                im /= 255
                if len(im.shape) == 3:
                    im = im[None]

            with dt[1]:
                pred = model(im, augment=False, visualize=False)

            with dt[2]:
                pred = non_max_suppression(pred, conf_thres, iou_thres, [PERSON_CLASS_ID], False, max_det=max_det)

            for i, det in enumerate(pred):
                seen += 1
                if webcam:
                    p, im0, frame = path[i], im0s[i].copy(), dataset.count
                else:
                    p, im0, frame = path, im0s.copy(), getattr(dataset, "frame", 0)

                p = Path(p)
                save_path = str(save_dir / p.name)
                stream_key = str(p if not webcam else i)
                state = states.setdefault(stream_key, FallState(fall_ratio, fall_duration))
                last_print.setdefault(stream_key, 0.0)

                annotator = Annotator(im0, line_width=line_thickness, example=str(names))
                detections = []

                if len(det):
                    det[:, :4] = scale_boxes(im.shape[2:], det[:, :4], im0.shape).round()
                    for *xyxy, conf, cls in reversed(det):
                        bbox = [float(v) for v in xyxy]
                        class_id = int(cls)
                        confidence = float(conf)
                        detections.append({
                            "class_id": class_id,
                            "confidence": confidence,
                            "bbox": bbox,
                        })
                        if save_img or view_img:
                            label = None if hide_labels else (
                                names[class_id] if hide_conf else f"{names[class_id]} {confidence:.2f}"
                            )
                            annotator.box_label(bbox, label, color=colors(class_id, True))

                person = choose_largest_person(detections, person_conf)
                aspect_ratio = compute_aspect_ratio(person["bbox"]) if person else None
                low_posture = (
                    is_low_posture(
                        person["bbox"],
                        im0.shape,
                        center_y_threshold=low_center_y,
                        bottom_y_threshold=low_bottom_y,
                        max_height_ratio=low_max_height,
                        min_aspect_ratio=low_min_ratio,
                    )
                    if person
                    else False
                )
                now = video_event_time(vid_cap, time.time())
                fall_alert = state.update(now, person is not None, aspect_ratio, low_posture)
                result = build_ai_result(person, aspect_ratio, low_posture, fall_alert, state, now)

                if person and (save_img or view_img):
                    x1, y1, x2, y2 = [int(v) for v in person["bbox"]]
                    cv2.rectangle(im0, (x1, y1), (x2, y2), (0, 0, 255) if fall_alert else (0, 255, 255), 3)

                status_text = (
                    "FALL ALERT"
                    if fall_alert
                    else f"low posture {aspect_ratio:.2f}" if low_posture and aspect_ratio is not None
                    else f"ratio={aspect_ratio:.2f}" if aspect_ratio is not None
                    else "no person"
                )
                status_color = (0, 0, 255) if fall_alert else (0, 255, 255)
                cv2.putText(im0, status_text, (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.2, status_color, 3)

                if now - last_print[stream_key] >= print_interval or fall_alert:
                    line = json.dumps(result, ensure_ascii=False)
                    print(line, flush=True)
                    if json_file:
                        json_file.write(line + "\n")
                        json_file.flush()
                    last_print[stream_key] = now

                im0 = annotator.result()
                if view_img:
                    if str(p) not in windows:
                        windows.append(str(p))
                        cv2.namedWindow(str(p), cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
                        cv2.resizeWindow(str(p), im0.shape[1], im0.shape[0])
                    cv2.imshow(str(p), im0)
                    cv2.waitKey(1)

                if save_img:
                    if dataset.mode == "image":
                        cv2.imwrite(save_path, im0)
                    else:
                        if vid_path[i] != save_path:
                            vid_path[i] = save_path
                            if isinstance(vid_writer[i], cv2.VideoWriter):
                                vid_writer[i].release()
                            if vid_cap:
                                fps = vid_cap.get(cv2.CAP_PROP_FPS)
                                w = int(vid_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                                h = int(vid_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                            else:
                                fps, w, h = 30, im0.shape[1], im0.shape[0]
                            save_path = str(Path(save_path).with_suffix(".mp4"))
                            vid_writer[i] = cv2.VideoWriter(save_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
                        vid_writer[i].write(im0)

        t = tuple(x.t / seen * 1e3 for x in dt) if seen else (0, 0, 0)
        LOGGER.info(f"Speed: %.1fms pre-process, %.1fms inference, %.1fms NMS per image at shape {(1, 3, *imgsz)}" % t)
        if save_img:
            LOGGER.info(f"Results saved to {colorstr('bold', save_dir)}")
        if json_file:
            LOGGER.info(f"JSON lines saved to {json_file.name}")
    finally:
        if json_file:
            json_file.close()


def main(opt):
    run(**vars(opt))


if __name__ == "__main__":
    main(parse_opt())
